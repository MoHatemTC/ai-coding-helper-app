from langchain_core.messages import HumanMessage
from app.graph.correctness import correctness_node
from app.schemas.graph import GraphState


def test_correctness_node_returns_findings():
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