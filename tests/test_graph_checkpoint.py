"""Tests for code/language persistence across checkpointed turns (F0.3)."""

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph

from app.schemas.graph import GraphState


def _passthrough(state: GraphState) -> dict:
    """A no-op node — just returns the state unchanged."""
    return {}


def _build_test_graph():
    """Build a minimal single-node graph backed by an in-memory checkpointer."""
    builder = StateGraph(GraphState)
    builder.add_node("noop", _passthrough)
    builder.set_entry_point("noop")
    builder.set_finish_point("noop")
    return builder.compile(checkpointer=MemorySaver())


def test_code_persists_across_checkpointed_turns():
    """Code submitted on turn 1 should still be present in state on turn 2."""
    graph = _build_test_graph()
    config: RunnableConfig = {"configurable": {"thread_id": "test-thread-1"}}

    graph.invoke(
        GraphState(
            code="def add(a, b):\n    return a + b",
            language="python",
        ),
        config,
    )

    graph.invoke(GraphState(), config)

    state = graph.get_state(config)
    assert state.values["code"] == "def add(a, b):\n    return a + b"
    assert state.values["language"] == "python"


def test_code_is_none_for_a_fresh_thread():
    """A new thread_id should start with no code, proving isolation between sessions."""
    graph = _build_test_graph()
    config: RunnableConfig = {"configurable": {"thread_id": "test-thread-2"}}

    graph.invoke(GraphState(), config)

    state = graph.get_state(config)
    assert state.values.get("code") is None
