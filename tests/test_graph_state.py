"""Tests for the graph state."""

from app.schemas.graph import GraphState
from app.schemas.review import Category, Finding, Severity


def test_graph_state_defaults():
    """GraphState should initialize with default values."""
    state = GraphState()

    assert state.messages == []
    assert state.findings == []
    assert state.long_term_memory == ""


def test_graph_state_accepts_findings():
    """GraphState should store review findings."""
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


def test_graph_state_accepts_code_and_language():
    """GraphState should store submitted code and language."""
    state = GraphState(code="def add(a, b):\n    return a + b", language="python")

    assert state.code == "def add(a, b):\n    return a + b"
    assert state.language == "python"


def test_graph_state_code_and_language_default_to_none():
    """code/language should default to None when not provided."""
    state = GraphState()

    assert state.code is None
    assert state.language is None
