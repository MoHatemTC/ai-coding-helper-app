"""LangGraph review node: correctness."""

from typing import Any

from app.schemas.graph import GraphState
from app.tools.review_tool import review_correctness


def correctness_node(state: GraphState) -> dict[str, Any]:
    """Run the rule-based correctness review against the submitted code.

    Reads: sanitized_code (falls back to code -- identical by the time this
        node runs, since it's only reached after inbound DLP passes clean)
    Writes: findings (appended via the shared operator.add reducer -- safe
        to run concurrently with security_review_node and
        performance_review_node)
    """
    code = state.get("sanitized_code") or state.get("code")
    if not code:
        return {"findings": []}

    findings = review_correctness(code)
    return {"findings": [finding.model_dump() for finding in findings]}
