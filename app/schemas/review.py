"""Pydantic schemas for review data."""

from enum import Enum

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

    file_path: str = Field(default="", description="The file path where the finding was discovered")
    line: int = Field(ge=1, description="1-based source code line")
    severity: Severity = Field(description="The severity level of the finding")
    category: Category = Field(description="The category of the finding")
    message: str = Field(description="A description of the finding")
    rationale: str = Field(description="The justification for the finding")


class CodeReviewResponse(BaseModel):
    """Wrapper schema for structured LLM output containing multiple findings."""

    findings: list[Finding] = Field(description="The list of code review findings")
    summary: str = Field(default="", description="A brief summary of the review")
