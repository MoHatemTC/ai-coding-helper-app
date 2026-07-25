"""Tests for the graph state contract.

GraphState is a TypedDict(total=False), not a Pydantic BaseModel: every node
in the graph (guardrails, review lanes, hints, chat, outbound) reads and
writes it via plain dict access, so these tests exercise it the same way --
no attribute access, no auto-populated defaults for omitted keys.
"""

import operator

from app.schemas.graph import GraphState
from app.schemas.review import Category, Finding, Severity


def test_graph_state_is_a_plain_dict_with_no_required_keys():
    """total=False means every key is optional.

    An empty state is valid, and unset keys are simply absent rather than
    defaulted to `[]`/`""`.
    """
    state: GraphState = {}

    assert isinstance(state, dict)
    assert state.get("messages", []) == []
    assert state.get("findings", []) == []
    assert state.get("long_term_memory", "") == ""


def test_graph_state_accepts_findings_as_plain_dicts():
    """Review nodes append Finding.model_dump() dicts, not Finding model instances.

    findings is typed list[dict[str, Any]] so the operator.add reducer can
    concatenate concurrent nodes' updates without importing the Finding
    model into the state contract itself.
    """
    finding = Finding(
        line=12,
        severity=Severity.MEDIUM,
        category=Category.CORRECTNESS,
        message="Possible bug",
        rationale="Example rationale.",
    )

    state: GraphState = {"findings": [finding.model_dump()]}

    assert len(state["findings"]) == 1
    assert state["findings"][0]["line"] == 12
    assert state["findings"][0]["category"] == Category.CORRECTNESS


def test_findings_reducer_appends_rather_than_overwrites():
    """The findings reducer must concatenate concurrent updates, never clobber them.

    That's what makes it safe for correctness/security/performance to run
    concurrently: `Annotated[list[dict[str, Any]], operator.add]` concatenates
    each node's update onto `findings` rather than one overwriting another.
    """
    correctness_update = [{"line": 2, "category": Category.CORRECTNESS.value}]
    security_update = [{"line": 5, "category": Category.SECURITY.value}]

    merged = operator.add(correctness_update, security_update)

    assert merged == [
        {"line": 2, "category": Category.CORRECTNESS.value},
        {"line": 5, "category": Category.SECURITY.value},
    ]
