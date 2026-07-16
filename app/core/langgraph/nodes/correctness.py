"""LangGraph review nodes."""

from app.schemas.graph import GraphState
from app.tools.review_tool import review_correctness


def correctness_node(state: GraphState):
    """Run the correctness review and return structured findings."""
    if not state.messages:
        return {"findings": []}

    code = state.messages[-1].content

    findings = review_correctness(code)

    return {
        "findings": findings,
    }