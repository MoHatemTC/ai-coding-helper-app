"""Performance & best-practice review node.

Maps to PRD F1.5 (Sprint 1 groundwork for the Performance & Best-Practices
lane): scaffolds a node that emits typed findings (category=performance /
style) using the shared Finding schema, and proves it works via a real
LLM call.

Schema notes:
  - `line: int` — single line number, per Aly's committed schema.
  - `category` has four top-level values: correctness, security,
    performance, style. `best_practice` is NOT its own category — it's a
    sub-label folded under `style` (alongside naming/formatting/duplication/
    SOLID/design-pattern issues), per confirmation from Aly.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.services.llm import llm_service

# ---------------------------------------------------------------------------
# Shared schema. Swap for the real `app.schemas` import once confirmed —
# only this block should need to change.
# ---------------------------------------------------------------------------


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
    """Shared finding shape per PRD F1.1: location, severity, category, message, rationale."""

    line: int = Field(..., ge=1, description="1-based line number the finding refers to")
    severity: Severity
    category: Category
    message: str = Field(..., description="Short, specific description of the issue")
    rationale: str = Field(..., description="Why this matters / the concept behind it")


class PerformanceReviewResult(BaseModel):
    """Wrapper model for structured LLM output.

    Used as the top-level response schema for
    `llm_service.call(..., response_format=...)`, since
    `with_structured_output` requires a single Pydantic model rather than
    a bare list.
    """

    findings: List[Finding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Sub-labels this lane looks for. NOT part of the shared Finding schema
# (Aly owns that) — but ACTIVELY enforced below via PerformanceFindingDraft,
# not just documented. The LLM is asked to return one of these enum values;
# Pydantic rejects anything else at parse time. Only after validation does
# the subtype get folded into the shared Finding's `message` field as a
# "[tag]" prefix, so the shared contract stays exactly what Aly expects.
# ---------------------------------------------------------------------------


class PerformanceIssueType(str, Enum):
    """Valid subtypes when category=performance."""

    ALGORITHMIC_COMPLEXITY = "algorithmic_complexity"
    REDUNDANT_COMPUTATION = "redundant_computation"
    INEFFICIENT_DATA_STRUCTURE = "inefficient_data_structure"
    UNNECESSARY_IO_IN_LOOP = "unnecessary_io_in_loop"
    RESOURCE_HANDLING = "resource_handling"


class StyleSubtype(str, Enum):
    """Valid subtypes when category=style."""

    BEST_PRACTICE = "best_practice"  # SOLID / missing abstraction / design pattern / duplication
    CODE_DUPLICATION = "code_duplication"
    SOLID_VIOLATION = "solid_violation"
    MISSING_ABSTRACTION = "missing_abstraction"
    DESIGN_PATTERN_OPPORTUNITY = "design_pattern_opportunity"
    NAMING_READABILITY = "naming_readability"
    FORMATTING = "formatting"


class PerformanceFindingDraft(BaseModel):
    """Internal schema targeted by the LLM.

    Extends the shared Finding schema with a Pydantic-validated subtype field.
    This ensures the LLM can only return values defined in
    PerformanceIssueType or StyleSubtype. Invalid subtype values will fail
    validation instead of being accepted as arbitrary text.
    """

    line: int = Field(..., ge=1)
    severity: Severity
    category: Category
    issue_type: Optional[PerformanceIssueType] = None
    style_subtype: Optional[StyleSubtype] = None
    message: str
    rationale: str


class PerformanceReviewDraft(BaseModel):
    """Structured response schema requested from the LLM.

    The validated response is converted into PerformanceReviewResult so the
    rest of the review pipeline operates on the shared Finding schema.
    """

    findings: List[PerformanceFindingDraft] = Field(default_factory=list)


def _draft_to_finding(draft: PerformanceFindingDraft) -> Finding:
    """Convert a validated draft into a shared Finding.

    The subtype is incorporated into the message before converting to the
    shared Finding schema. By the time this function is called, Pydantic has
    already validated that the subtype is a valid enum value.
    """
    tag = draft.issue_type or draft.style_subtype
    message = f"[{tag.value}] {draft.message}" if tag else draft.message
    return Finding(
        line=draft.line,
        severity=draft.severity,
        category=draft.category,
        message=message,
        rationale=draft.rationale,
    )


SYSTEM_PROMPT = """You are a senior code reviewer focused ONLY on two things:

1. category="performance": set issue_type to exactly one of:
   algorithmic_complexity, redundant_computation, inefficient_data_structure,
   unnecessary_io_in_loop, resource_handling.
2. category="style": set style_subtype to exactly one of:
   best_practice, code_duplication, solid_violation, missing_abstraction,
   design_pattern_opportunity, naming_readability, formatting.
   Use "best_practice" for structural issues (duplication, SOLID
   violations, missing abstractions, a design pattern that would fit
   better); use the more specific values for surface-level naming/
   formatting issues when they clearly apply.

Set issue_type when category=performance, style_subtype when
category=style. Never invent a value outside these lists — pick the
closest match.

Do not comment on correctness bugs or security vulnerabilities — other
reviewers handle those.

Name a better approach CONCEPTUALLY. Never paste a full rewrite of the code.

Every finding must reference a single line number (an integer) from the
numbered code you are given — pick the most representative line if the
issue spans several. If there is nothing worth flagging, return no
findings."""


def _number_lines(code: str) -> str:
    """Prefix each line with its number so the LLM can reference real lines."""
    return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(code.splitlines()))


async def run_performance_review(code: str, language: Optional[str] = None) -> List[Finding]:
    """Run the performance/best-practice review node.

    This is the function that would eventually be wired into
    `app/core/langgraph/graph.py` as a graph node (Sprint 2 work, once the
    review sub-graph exists) — e.g.:

        graph_builder.add_node("performance_review", performance_review_node)

    Args:
        code: The submitted source code.
        language: Optional language hint (e.g. "python").

    Returns:
        A list of Finding objects, category=performance or category=style.
    """
    lang_hint = f"Language: {language}\n\n" if language else ""
    user_content = f"{lang_hint}Numbered code:\n{_number_lines(code)}"

    messages = [
        ("system", SYSTEM_PROMPT),
        ("user", user_content),
    ]

    result: PerformanceReviewDraft = await llm_service.call(
        messages,
        response_format=PerformanceReviewDraft,
    )
    return [_draft_to_finding(draft) for draft in result.findings]


# ---------------------------------------------------------------------------
# "Produce a baseline finding via the LLM" — run this file directly to prove
# the node works end-to-end against a real code sample.
#   python -m app.core.langgraph.nodes.performance_node
# (adjust the module path to wherever you actually place this file)
#
# NOTE: this uses llm_service (OpenAI-based registry). Use
# run_baseline_with_openrouter.py for testing against a free OpenRouter
# model directly, without touching shared registry/service code.
# ---------------------------------------------------------------------------

SAMPLE_CODE = """def find_duplicates(items):
    duplicates = []
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i] == items[j] and items[i] not in duplicates:
                duplicates.append(items[i])
    return duplicates
"""

if __name__ == "__main__":

    async def _main() -> None:
        findings = await run_performance_review(SAMPLE_CODE, language="python")
        if not findings:
            print("No findings returned — check the prompt or sample code.")
        for f in findings:
            print(f.model_dump_json(indent=2))

    asyncio.run(_main())
