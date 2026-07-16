"""Pydantic schemas for skill profile data."""

from enum import Enum
from typing import List

from pydantic import BaseModel, Field


class SkillLevel(str, Enum):
    """Skill level of the user."""

    BEGINNER = "Beginner"
    INTERMEDIATE = "Intermediate"
    ADVANCED = "Advanced"


class Weakness(BaseModel):
    """Weakness of the user in a topic."""

    topic: str = Field(description="The topic that user is weak in")
    description: str = Field(description="Description of user's weakness in the topic")


class SkillProfile(BaseModel):
    """Skill profile of the user."""

    skill_level: SkillLevel = Field(description="Skill level of the user")
    weaknesses: List[Weakness] = Field(description="List of weaknesses of the user in each topic")
    all_searched_topics: List[str] = Field(description="List of topics searched by the user")
