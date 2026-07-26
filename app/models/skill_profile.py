"""DB model for structured skill profile rows — one row per (user, skill, category)."""

from datetime import datetime, timezone


from sqlmodel import Field, SQLModel

from app.schemas.skill_profile import ProficiencyLevel, SkillCategory


class SkillProfileEntry(SQLModel, table=True):
    """One skill assessment for one user.

    `skill_key` is the normalized (lowercased, stripped) match key — it's what
    upsert/remove match against, so casing drift from the LLM ("python" vs
    "Python") never creates duplicate rows. `skill` keeps the display casing.
    """

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)

    skill: str
    skill_key: str = Field(index=True)
    category: SkillCategory
    proficiency: ProficiencyLevel | None = None
    detail: str

    evidence_count: int = Field(default=1)  # times this entry was upserted/reinforced
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
