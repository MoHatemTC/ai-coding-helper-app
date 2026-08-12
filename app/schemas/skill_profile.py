"""Structured-output schemas for the skill profile ACE pipeline.

These are the Pydantic models bound to the LLM via `with_structured_output`.
They are intentionally NOT the DB models (see app/models/skill_profile.py) —
the LLM should never see or set ids, timestamps, or evidence counters.
"""

from enum import Enum

from pydantic import BaseModel, Field


class SkillCategory(str, Enum):
    """Fixed category set — controls grouping order when rendered for the prompt."""

    LANGUAGE = "language"
    FRAMEWORK = "framework"
    TOOL = "tool"
    STRENGTH = "strength"
    STRUGGLE = "struggle"
    PATTERN = "pattern"
    PROJECT_CONTEXT = "project_context"


class ProficiencyLevel(str, Enum):
    """Fixed proficiency level set — controls grouping order when rendered for the prompt."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SkillAssessment(BaseModel):
    """A single skill entry the model wants to add or correct."""

    skill: str = Field(
        description=(
            "Canonical name of the skill/language/framework/tool, or a short "
            "phrase for a strength/struggle/pattern, e.g. 'Python', 'FastAPI', "
            "'off-by-one errors in loops'."
        )
    )
    category: SkillCategory
    proficiency: ProficiencyLevel | None = Field(
        default=None,
        description=(
            "Required for language/framework/tool. Must be null for "
            "strength/struggle/pattern/project_context — those aren't skill levels."
        ),
    )
    detail: str = Field(
        description=(
            "One concise, concrete clause of evidence from the conversation. "
            "E.g. 'comfortable with async/await, still confuses decorators with "
            "higher-order functions.' No filler, no restating the skill name."
        )
    )


class ProfileDelta(BaseModel):
    """The full output of a single generate+reflect pass: a minimal diff.

    The model sees the CURRENT profile as context and is asked to propose only
    what should change — this is what lets generation and reflection collapse
    into one call: reflection here is just "does the current entry already
    cover this, or does it need a correction" evaluated inline while generating.
    """

    upsert: list[SkillAssessment] = Field(
        default_factory=list,
        description=(
            "Skills that are new (clear evidence, not yet in the profile) or "
            "whose existing entry is now wrong/outdated and needs correcting. "
            "Do NOT include skills that are already accurately captured — "
            "leave those alone entirely."
        ),
    )
    remove: list[str] = Field(
        default_factory=list,
        description=(
            "Skill names currently in the profile that this conversation "
            "directly contradicts (e.g. user says they've never touched X, "
            "or a prior entry was clearly a misread). Rare — only with "
            "explicit contradicting evidence, never just 'not mentioned again'."
        ),
    )
