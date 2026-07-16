"""LangGraph review nodes."""

from app.schemas.graph import GraphState
from app.tools.review_tool import review_correctness


def correctness_node(state: GraphState):
    """Run the correctness review and return structured findings."""
    if not state.messages:
        return {"findings": []}

    content = state.messages[-1].content

    if not isinstance(content, str):
        return {"findings": []}

    findings = review_correctness(content)

    return {"findings": findings}