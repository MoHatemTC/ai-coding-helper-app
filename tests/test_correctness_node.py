"""Tests for the correctness review node.

correctness_node reads the *submitted code* (state["code"], preferring
state["sanitized_code"] when DLP redaction ran), not the chat transcript --
a user's prose in `messages` is not code to be reviewed.
"""

from app.core.langgraph.nodes.correctness import correctness_node
from app.schemas.graph import GraphState


def test_correctness_node_returns_findings():
    """The node should detect correctness issues in the submitted code."""
    state: GraphState = {
        "code": """
x = 10
y = x / 0
""",
    }

    result = correctness_node(state)

    assert "findings" in result
    assert len(result["findings"]) == 1


def test_correctness_node_returns_empty_for_correct_code():
    """The node should return no findings for correct code."""
    state: GraphState = {
        "code": """
x = 10
y = x / 2
""",
    }

    result = correctness_node(state)

    assert result["findings"] == []


def test_correctness_node_prefers_sanitized_code_over_raw_code():
    """When DLP redaction ran, the node should review the sanitized code, not the raw original.

    The raw (possibly secret-containing) code is never sent to a static
    review pass once a sanitized version exists.
    """
    state: GraphState = {
        "code": 'api_key = "sk-abcdefghijklmnopqrstuvwx"\ny = 10 / 0',  # pragma: allowlist secret
        "sanitized_code": 'api_key = "[REDACTED_SECRET]"\ny = 10 / 0',
    }

    result = correctness_node(state)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["line"] == 2


def test_correctness_node_returns_empty_when_no_code_submitted():
    """A turn with no submitted code should not error.

    E.g. a general question -- it simply has nothing to review.
    """
    state: GraphState = {"user_query": "How do binary search trees work?"}

    result = correctness_node(state)

    assert result["findings"] == []
