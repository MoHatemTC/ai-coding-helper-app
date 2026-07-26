"""Schemas for long-term memory consolidation."""

from pydantic import BaseModel, Field


class ConsolidatedFacts(BaseModel):
    """Structured output for memory consolidation."""

    facts: list[str] = Field(
        description=(
            "List of consolidated facts about the user's coding skills, "
            "preferences, and project context. Each fact should be a concise "
            "sentence capturing one distinct piece of information."
        ),
    )
