"""Schemas used by the code review workflow."""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Severity levels for a review finding."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Category(str, Enum):
    """Category classifications for a review finding."""

    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"


class Finding(BaseModel):
    """Schema representing a single issue discovered during code review."""

    line: int = Field(
        ge=1,
        description=(
            "The 1-based line number in the source code where the issue occurs. "
            "Return the line that best identifies the root cause of the finding."
        ),
    )

    severity: Severity = Field(
        description=(
            "The severity of the issue. "
            "Use LOW for minor concerns, MEDIUM for issues that should be addressed, "
            "HIGH for serious defects that can cause incorrect behavior, and "
            "CRITICAL for issues likely to cause crashes, security vulnerabilities, "
            "or data loss."
        ),
    )

    category: Category = Field(
        description=(
            "The primary category of the issue. Choose exactly one of: correctness, security, performance, or style."
        ),
    )

    message: str = Field(
        description=(
            "A short, user-facing summary of the issue in one sentence. "
            "Clearly describe what is wrong without explaining why."
        ),
    )

    rationale: str = Field(
        description=(
            "A concise technical explanation of why this issue is a problem, "
            "including the potential impact on correctness, reliability, security, "
            "or performance when appropriate."
        ),
    )


class HintLevel(str, Enum):
    """Progressive tiers of mentorship for escalating user guidance."""

    NUDGE = "nudge"
    DIRECTION = "direction"
    CONCRETE_STEP = "concrete_step"


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


class MentorResponse(BaseModel):
    """Represents the structured response returned by the AI Mentor."""

    understanding: str = Field(
        description="A concise statement confirming the mentor's understanding of the user's intent, goal, or question."
    )

    review: Optional[str] = Field(
        default=None,
        description="Constructive feedback on the user's code or approach, highlighting strengths, weaknesses, potential bugs, edge cases, performance concerns, or security risks without providing the complete solution.",
    )

    explanation: str = Field(
        description="An explanation of the reasoning behind the review or recommendation, helping the learner understand the underlying concepts, engineering principles, or trade-offs."
    )

    hint: Optional[str] = Field(
        default=None,
        description="A progressive hint that guides the learner toward discovering the solution independently without revealing the final answer.",
    )

    next_step: Optional[str] = Field(
        default=None,
        description="A concrete action or exercise that encourages the learner to continue solving the problem independently.",
    )

    additional_context: Optional[str] = Field(
        default=None,
        description="Requests additional information when the available context is insufficient to provide accurate guidance. This supports the mentor's principle of honest uncertainty.",
    )
