"""Tests for the correctness review node."""

from langchain_core.messages import HumanMessage
from app.core.langgraph.nodes.correctness import correctness_node
from app.schemas.graph import GraphState


def test_correctness_node_returns_findings():
    """The node should detect correctness issues."""
    state = GraphState(
        messages=[
            HumanMessage(
                content="""
x = 10
y = x / 0
"""
            )
        ]
    )

    result = correctness_node(state)

    assert "findings" in result
    assert len(result["findings"]) == 1


def test_correctness_node_returns_empty():
    """The node should return no findings for correct code."""
    state = GraphState(
        messages=[
            HumanMessage(
                content="""
x = 10
y = x / 2
"""
            )
        ]
    )

    result = correctness_node(state)

    assert result["findings"] == []