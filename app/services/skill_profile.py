"""Skill profile service — ACE pipeline over structured DB rows.

Two phases instead of three:
1. propose_delta() — ONE LLM call, structured output. The model sees the
   current profile as context, so "reflection" (is this entry still right?)
   happens inline with generation, in the same pass. Output is a minimal
   diff (ProfileDelta), not a full profile rewrite.
2. curate()        — deterministic DB upsert/delete from that diff. No LLM,
   no parsing, no regex — just matching on `skill_key` + `category`.

Structured output (`with_structured_output(ProfileDelta)`) replaces the old
JSON-in-a-code-fence parsing entirely — the LLM call either returns a valid
ProfileDelta or raises, there's no ambiguous partial-parse state.
"""

import asyncio
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import logger
from app.core.prompts.skill_profile import SKILL_PROFILE_DELTA_PROMPT
from app.models.skill_profile import SkillProfileEntry
from app.schemas.skill_profile import ProfileDelta, SkillCategory
from app.services.database import database_service
from app.services.llm.registry import LLMRegistry

# Fixed render order — independent of DB insertion order or category enum order.
_CATEGORY_DISPLAY_ORDER = [
    SkillCategory.LANGUAGE,
    SkillCategory.FRAMEWORK,
    SkillCategory.TOOL,
    SkillCategory.STRENGTH,
    SkillCategory.STRUGGLE,
    SkillCategory.PATTERN,
    SkillCategory.PROJECT_CONTEXT,
]
_CATEGORY_LABELS = {
    SkillCategory.LANGUAGE: "Languages",
    SkillCategory.FRAMEWORK: "Frameworks",
    SkillCategory.TOOL: "Tools",
    SkillCategory.STRENGTH: "Strengths",
    SkillCategory.STRUGGLE: "Struggles",
    SkillCategory.PATTERN: "Coding patterns",
    SkillCategory.PROJECT_CONTEXT: "Project context",
}


class SkillProfileService:
    """Manages per-user skill profiles as structured rows via a 2-phase ACE pipeline."""

    def __init__(self) -> None:
        """Initialize the skill profile service."""
        self._timers: dict[str, asyncio.Task] = {}

    # ── DB reads / writes (sync — always call via asyncio.to_thread from async code) ──

    def load_entries(self, user_id: int) -> list[SkillProfileEntry]:
        """Load all skill rows for a user, in fixed display order."""
        try:
            with Session(database_service.engine) as session:
                rows = session.exec(select(SkillProfileEntry).where(SkillProfileEntry.user_id == user_id)).all()
            order = {cat: i for i, cat in enumerate(_CATEGORY_DISPLAY_ORDER)}
            return sorted(rows, key=lambda r: (order.get(r.category, 99), r.skill.lower()))
        except Exception:
            logger.exception("skill_profile_load_failed", user_id=user_id)
            return []

    def curate(self, user_id: int, delta: ProfileDelta) -> None:
        """Deterministic delta merge — no LLM call. Applies upserts and removals by skill_key."""
        try:
            with Session(database_service.engine) as session:
                now = datetime.now(timezone.utc)

                for assessment in delta.upsert:
                    skill_key = assessment.skill.strip().lower()
                    existing = session.exec(
                        select(SkillProfileEntry).where(
                            SkillProfileEntry.user_id == user_id,
                            SkillProfileEntry.skill_key == skill_key,
                            SkillProfileEntry.category == assessment.category,
                        )
                    ).first()
                    if existing:
                        existing.proficiency = assessment.proficiency
                        existing.detail = assessment.detail
                        existing.evidence_count += 1
                        existing.updated_at = now
                        session.add(existing)
                    else:
                        session.add(
                            SkillProfileEntry(
                                user_id=user_id,
                                skill=assessment.skill.strip(),
                                skill_key=skill_key,
                                category=assessment.category,
                                proficiency=assessment.proficiency,
                                detail=assessment.detail,
                                evidence_count=1,
                                created_at=now,
                                updated_at=now,
                            )
                        )

                for skill_name in delta.remove:
                    skill_key = skill_name.strip().lower()
                    rows = session.exec(
                        select(SkillProfileEntry).where(
                            SkillProfileEntry.user_id == user_id,
                            SkillProfileEntry.skill_key == skill_key,
                        )
                    ).all()
                    for row in rows:
                        session.delete(row)

                session.commit()
                logger.info(
                    "skill_profile_curated",
                    user_id=user_id,
                    upserted=len(delta.upsert),
                    removed=len(delta.remove),
                )
        except Exception:
            logger.exception("skill_profile_curate_failed", user_id=user_id)

    # ── Rendering for system-prompt injection (not markdown — plain grouped text) ──

    def render_for_prompt(self, user_id: int, max_entries: int = 50) -> str:
        """Build the compact string injected into the mentor's system prompt.

        Args:
            user_id: The user whose profile to render.
            max_entries: Cap on total entries to prevent unbounded system prompt growth.
                         Entries beyond the cap are silently dropped (lowest evidence first).
        """
        entries = self.load_entries(user_id)
        if not entries:
            return "No skill profile recorded yet."

        # Soft cap: if over limit, drop lowest-evidence entries
        if len(entries) > max_entries:
            entries = sorted(entries, key=lambda e: -e.evidence_count)[:max_entries]

        by_category: dict[SkillCategory, list[SkillProfileEntry]] = {}
        for entry in entries:
            by_category.setdefault(entry.category, []).append(entry)

        lines: list[str] = []
        for category in _CATEGORY_DISPLAY_ORDER:
            cat_entries = by_category.get(category)
            if not cat_entries:
                continue
            lines.append(f"{_CATEGORY_LABELS[category]}:")
            for e in cat_entries:
                prof = f" ({e.proficiency.value})" if e.proficiency else ""
                lines.append(f"- {e.skill}{prof}: {e.detail}")
        return "\n".join(lines)

    async def render_for_prompt_async(self, user_id: int) -> str:
        """Async wrapper — calls render_for_prompt in a thread so it doesn't block the event loop."""
        return await asyncio.to_thread(self.render_for_prompt, user_id)

    # ── ACE pipeline (2 phases) ──────────────────────────────────────

    async def propose_delta(self, user_id: int, conversation: str) -> ProfileDelta:
        """Single structured-output LLM call — generation + reflection merged."""
        existing_text = await asyncio.to_thread(self.render_for_prompt, user_id)

        llm = LLMRegistry.get(settings.SKILL_PROFILE_MODEL, temperature=0.1, max_tokens=800)
        structured_llm = llm.with_structured_output(ProfileDelta)

        try:
            result = await structured_llm.ainvoke(
                [
                    HumanMessage(
                        content=SKILL_PROFILE_DELTA_PROMPT.format(
                            existing_profile=existing_text,
                            conversation=conversation,
                        )
                    )
                ]
            )
            # with_structured_output should already return a ProfileDelta, but
            # guard in case the registry wraps it in a dict at some point.
            return result if isinstance(result, ProfileDelta) else ProfileDelta.model_validate(result)
        except Exception:
            logger.exception("skill_profile_delta_failed", user_id=user_id)
            return ProfileDelta()  # empty delta = safe no-op, never corrupts existing rows

    async def update(self, user_id: int, conversation: str) -> str:
        """Full pipeline: propose_delta (LLM) → curate (deterministic) → render."""
        delta = await self.propose_delta(user_id, conversation)
        if delta.upsert or delta.remove:
            await asyncio.to_thread(self.curate, user_id, delta)
        return await asyncio.to_thread(self.render_for_prompt, user_id)

    # ── Silence monitor ────────────────────────────────────────────

    def schedule_update(self, user_id: int, conversation: str) -> None:
        """Schedule a skill profile update after N seconds of silence."""
        uid_str = str(user_id)
        if uid_str in self._timers:
            self._timers[uid_str].cancel()
        self._timers[uid_str] = asyncio.create_task(self._silence_timer(user_id, conversation))
        logger.debug("skill_profile_update_scheduled", user_id=user_id)

    async def _silence_timer(self, user_id: int, conversation: str) -> None:
        try:
            await asyncio.sleep(settings.SKILL_PROFILE_SILENCE_SECONDS)
            await self.update(user_id, conversation)
            self._timers.pop(str(user_id), None)
        except asyncio.CancelledError:
            pass  # cancelled by a new message — expected
        except Exception:
            logger.exception("skill_profile_silence_timer_failed", user_id=user_id)
            self._timers.pop(str(user_id), None)

    def cancel_timer(self, user_id: int) -> None:
        """Cancel a scheduled skill profile update."""
        task = self._timers.pop(str(user_id), None)
        if task:
            task.cancel()

    async def shutdown(self) -> None:
        """Cancel all scheduled skill profile updates."""
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()


skill_profile_service = SkillProfileService()
