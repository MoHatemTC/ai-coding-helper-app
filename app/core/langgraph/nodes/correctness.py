"""LangGraph review node: correctness."""

from typing import Any

from app.schemas.graph import GraphState
from app.tools.review_tool import review_correctness


def correctness_node(state: GraphState) -> dict[str, Any]:
    """Run the rule-based correctness review against submitted code."""
    code = state.get("sanitized_code") or state.get("code")

    if not code:
        return {"findings": []}

    language = state.get("language")

    findings = review_correctness(
        code,
        language=language,
    )

    return {
        "findings": [
            finding.model_dump()
            for finding in findings
        ]
    }
