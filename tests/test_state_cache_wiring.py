"""Tests for state-snapshot caching wiring in the hint agent.

Verifies that ``aget_state`` reads are served from the per-session cache, that
the cache is refreshed after a successful run, and that an interrupted run
caches the interrupted state so the next turn resumes without a Postgres read.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.errors import GraphInterrupt

from app.core.langgraph.graph import LangGraphAgent
from app.schemas import Message


def _snapshot(*, next_nodes: list[str] | None = None, marker: str = "fresh") -> SimpleNamespace:
    """Build a minimal stand-in for a langgraph StateSnapshot."""
    tasks = (
        [SimpleNamespace(interrupts=[SimpleNamespace(value="Please provide your code sample.")])] if next_nodes else []
    )
    return SimpleNamespace(
        next=next_nodes or [],
        tasks=tasks,
        values={"messages": [], "summary": ""},
        marker=marker,
    )


class _FakeGraph:
    """Stand-in compiled graph: scripted states, records reads, optional interrupt."""

    def __init__(self) -> None:
        self.read_count = 0
        self.states: dict[str, SimpleNamespace] = {}
        self.interrupt_on_run = False

    async def aget_state(self, config: dict) -> SimpleNamespace:
        self.read_count += 1
        marker = config["configurable"]["thread_id"]
        return self.states.get(marker, _snapshot(marker=marker))

    async def ainvoke(self, input: dict, config: dict = None) -> dict:  # noqa: A002
        thread_id = config["configurable"]["thread_id"]
        if self.interrupt_on_run:
            self.states[thread_id] = _snapshot(next_nodes=["agent"], marker="interrupted")
            raise GraphInterrupt("interrupted")
        self.states[thread_id] = _snapshot(marker="post-run")
        return {"final_response": "Here is your hint."}


async def _build_agent() -> tuple[LangGraphAgent, _FakeGraph]:
    agent = LangGraphAgent()
    fake_graph = _FakeGraph()
    agent._graph = fake_graph
    return agent, fake_graph


@pytest.mark.asyncio
async def test_pre_run_state_read_hits_cache() -> None:
    agent, fake_graph = await _build_agent()
    message = Message(role="user", content="hello")

    first = await agent.get_response(message, "s1")
    assert first == [Message(role="assistant", content="Here is your hint.")]
    reads_after_first = fake_graph.read_count

    second = await agent.get_response(message, "s1")
    assert second == [Message(role="assistant", content="Here is your hint.")]
    assert fake_graph.read_count == reads_after_first + 1


@pytest.mark.asyncio
async def test_interrupt_state_is_cached_and_resumes() -> None:
    agent, fake_graph = await _build_agent()
    fake_graph.interrupt_on_run = True
    message = Message(role="user", content="review my code")

    response = await agent.get_response(message, "s1")
    assert response == [Message(role="assistant", content="Please provide your code sample.")]

    cached = agent._state_cache.get("s1")
    assert cached is not None
    assert cached.next == ["agent"]

    fake_graph.interrupt_on_run = False
    response = await agent.get_response(message, "s1")
    assert response == [Message(role="assistant", content="Here is your hint.")]


@pytest.mark.asyncio
async def test_error_invalidates_cached_state() -> None:
    agent, fake_graph = await _build_agent()
    agent._state_cache.set("s1", _snapshot(marker="cached"))

    async def _explode(input: dict, config: dict = None) -> dict:  # noqa: A002
        raise RuntimeError("boom")

    fake_graph.ainvoke = _explode
    message = Message(role="user", content="hello")

    with pytest.raises(RuntimeError, match="boom"):
        await agent.get_response(message, "s1")

    assert agent._state_cache.get("s1") is None


@pytest.mark.asyncio
async def test_chat_history_serves_from_cache() -> None:
    agent, fake_graph = await _build_agent()
    agent._state_cache.set(
        "s1",
        _snapshot(next_nodes=[], marker="cached"),
    )
    fake_graph.aget_state = AsyncMock(side_effect=AssertionError("should not hit PG"))

    history = await agent.get_chat_history("s1")
    assert history == []
    fake_graph.aget_state.assert_not_called()
