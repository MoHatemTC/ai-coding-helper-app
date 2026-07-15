from app.schemas.graph import GraphState
from app.schemas.review import Category, Finding, Severity


def test_graph_state_defaults():
    state = GraphState()

    assert state.messages == []
    assert state.findings == []
    assert state.long_term_memory == ""


def test_graph_state_accepts_findings():
    finding = Finding(
        line=12,
        severity=Severity.MEDIUM,
        category=Category.CORRECTNESS,
        message="Possible bug",
        rationale="Example rationale.",
    )

    state = GraphState(findings=[finding])

    assert len(state.findings) == 1
    assert state.findings[0].line == 12
