"""Skill profile model — one .md file per user."""

from sqlmodel import Column, Field, Text

from app.models.base import BaseModel


class SkillProfile(BaseModel, table=True):
    """Stores a markdown skill profile per user, updated by the ACE pipeline."""

    user_id: int = Field(foreign_key="user.id", primary_key=True)
    content: str = Field(
        default="",
        sa_column=Column(Text, nullable=False, server_default=""),
    )
    updated_at: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
