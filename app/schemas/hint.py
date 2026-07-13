"""Pydantic schemas for progressive AI mentorship hints."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class HintLevel(str, Enum):
    """Progressive tiers of mentorship for escalating user guidance."""

    NUDGE = "nudge"
    DIRECTION = "direction"
    CONCRETE_STEP = "concrete_step"


class FindingSchema(BaseModel):
    """Temporary representation of a code-review finding from Aly's node."""

    location: int = Field(description="Line number containing the code issue.")
    severity: str = Field(description="Severity of the issue, for example INFO, WARNING, or CRITICAL.")
    category: str = Field(description="Type of issue, for example correctness, style, or security.")
    message: str = Field(description="Plain-text explanation of what is wrong.")
    rationale: str = Field(description="Logical reason the issue should be addressed.")


class HintState(BaseModel):
    """LangGraph memory that records the current hint escalation state."""

    current_problem_id: Optional[str] = Field(
        default=None, description="ID of the active code problem. Used to reset hint history on problem switch."
    )
    current_level: HintLevel = Field(
        default=HintLevel.NUDGE,
        description="Current progressive mentorship level.",
    )
    history: List[HintLevel] = Field(
        default_factory=list,
        description="Previously delivered hint levels, in chronological order.",
    )


class HintResponse(BaseModel):
    """Structured response that the AI Mentor returns for a progressive hint."""

    level: HintLevel = Field(description="Progressive mentorship level for this hint.")
    hint_text: str = Field(description="Markdown-formatted progressive hint text.")
    socratic_question: str = Field(description="A single thought-provoking Socratic question for the user.")
