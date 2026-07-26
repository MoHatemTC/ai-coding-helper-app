"""Skill profile service — ACE pipeline + 30-minute silence monitor."""

import asyncio
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.core.logging import logger
from app.core.prompts.skill_profile import (
    SKILL_PROFILE_CURATION_PROMPT,
    SKILL_PROFILE_GENERATION_PROMPT,
    SKILL_PROFILE_REFLECTION_PROMPT,
)
from app.models.skill_profile import SkillProfile
from app.services.database import database_service
from app.services.llm.registry import LLMRegistry
from sqlmodel import Session


def _extract_text(content: object) -> str:
    """Extract plain text from LLM response content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


class SkillProfileService:
    """Manages per-user skill profiles via an ACE pipeline.

    Three-phase approach:
    1. generate() — build/update profile from conversation
    2. reflect() — quality-check the generated profile
    3. curate() — apply reflection feedback to produce final profile

    Also manages a 30-minute silence monitor that triggers background
    profile regeneration after inactivity.
    """

    def __init__(self) -> None:
        """Initialize the skill profile service."""
        self._timers: dict[str, asyncio.Task] = {}

    # ── DB operations ──────────────────────────────────────────────

    def load(self, user_id: int) -> str:
        """Load the skill profile markdown for a user. Returns empty string if none."""
        try:
            with Session(database_service.engine) as session:
                profile = session.get(SkillProfile, user_id)
                return profile.content if profile else ""
        except Exception:
            logger.exception("skill_profile_load_failed", user_id=user_id)
            return ""

    def save(self, user_id: int, content: str) -> None:
        """Upsert the skill profile markdown for a user."""
        try:
            with Session(database_service.engine) as session:
                existing = session.get(SkillProfile, user_id)
                now = datetime.now(timezone.utc).isoformat()
                if existing:
                    existing.content = content
                    existing.updated_at = now
                else:
                    session.add(SkillProfile(user_id=user_id, content=content, updated_at=now))
                session.commit()
                logger.info("skill_profile_saved", user_id=user_id, length=len(content))
        except Exception:
            logger.exception("skill_profile_save_failed", user_id=user_id)

    # ── ACE pipeline ───────────────────────────────────────────────

    async def generate(self, user_id: int, conversation: str) -> str:
        """Phase 1: Generate or update profile from conversation."""
        existing = self.load(user_id)
        existing_section = (
            f"Existing profile to update:\n{existing}" if existing else "No existing profile — create from scratch."
        )

        llm = LLMRegistry.get(settings.DEFAULT_LLM_MODEL, temperature=0.2, max_tokens=800)
        result = await llm.ainvoke(
            [
                HumanMessage(
                    content=SKILL_PROFILE_GENERATION_PROMPT.format(
                        existing_profile=existing_section,
                        conversation=conversation,
                    )
                )
            ]
        )
        return _extract_text(result.content)

    async def reflect(self, conversation: str, profile: str) -> dict:
        """Phase 2: Quality-check the generated profile."""
        llm = LLMRegistry.get(settings.DEFAULT_LLM_MODEL, temperature=0, max_tokens=500)
        result = await llm.ainvoke(
            [
                HumanMessage(
                    content=SKILL_PROFILE_REFLECTION_PROMPT.format(
                        conversation=conversation,
                        profile=profile,
                    )
                )
            ]
        )
        content = _extract_text(result.content)
        # Parse the JSON-like response
        import json

        try:
            # Try to extract JSON from the response
            if "```" in content:
                json_str = content.split("```")[1]
                if json_str.startswith("json"):
                    json_str = json_str[4:]
                return json.loads(json_str.strip())
            return json.loads(content)
        except (json.JSONDecodeError, IndexError):
            logger.warning("skill_profile_reflection_parse_failed", user_id=None)
            return {"correct": [], "incorrect": [], "missing": []}

    async def curate(self, profile: str, reflection: dict) -> str:
        """Phase 3: Apply reflection feedback to produce final profile."""
        llm = LLMRegistry.get(settings.DEFAULT_LLM_MODEL, temperature=0, max_tokens=800)
        result = await llm.ainvoke(
            [
                HumanMessage(
                    content=SKILL_PROFILE_CURATION_PROMPT.format(
                        profile=profile,
                        correct=", ".join(reflection.get("correct", [])) or "none",
                        incorrect=", ".join(f"{item}" for item in reflection.get("incorrect", [])) or "none",
                        missing=", ".join(reflection.get("missing", [])) or "none",
                    )
                )
            ]
        )
        return _extract_text(result.content)

    async def update(self, user_id: int, conversation: str) -> str:
        """Full ACE pipeline: generate → reflect → curate → save."""
        try:
            generated = await self.generate(user_id, conversation)
            reflection = await self.reflect(conversation, generated)
            final = await self.curate(generated, reflection)
            self.save(user_id, final)
            logger.info(
                "skill_profile_update_completed",
                user_id=user_id,
                profile_length=len(final),
            )
            return final
        except Exception:
            logger.exception("skill_profile_update_failed", user_id=user_id)
            return self.load(user_id)

    # ── Silence monitor ────────────────────────────────────────────

    def schedule_update(self, user_id: int, conversation: str) -> None:
        """Schedule a skill profile update after 30 minutes of silence.

        Cancels any existing timer for this user and creates a new one.
        """
        uid_str = str(user_id)
        if uid_str in self._timers:
            self._timers[uid_str].cancel()

        self._timers[uid_str] = asyncio.create_task(self._silence_timer(user_id, conversation))
        logger.debug("skill_profile_update_scheduled", user_id=user_id)

    async def _silence_timer(self, user_id: int, conversation: str) -> None:
        """Wait 30 minutes then run the ACE pipeline."""
        try:
            await asyncio.sleep(settings.SKILL_PROFILE_SILENCE_SECONDS)
            await self.update(user_id, conversation)
            self._timers.pop(str(user_id), None)
        except asyncio.CancelledError:
            pass  # Timer was cancelled by a new message — expected
        except Exception:
            logger.exception("skill_profile_silence_timer_failed", user_id=user_id)
            self._timers.pop(str(user_id), None)

    def cancel_timer(self, user_id: int) -> None:
        """Cancel any pending silence timer for a user."""
        uid_str = str(user_id)
        task = self._timers.pop(uid_str, None)
        if task:
            task.cancel()

    async def shutdown(self) -> None:
        """Cancel all pending timers on app shutdown."""
        for task in self._timers.values():
            task.cancel()
        self._timers.clear()


skill_profile_service = SkillProfileService()
