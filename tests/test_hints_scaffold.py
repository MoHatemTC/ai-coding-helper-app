"""Tests for progressive hint-node draft generation and retry-aware prompting."""

from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.core.langgraph.nodes.hints import generate_hint_node
from app.schemas import GraphState


def _state(query: str = "Why does int('abc') raise a ValueError?") -> GraphState:
    """Build a GraphState carrying a single human message."""
    return GraphState(messages=[HumanMessage(content=query)])


@pytest.mark.asyncio
async def test_hint_node_generates_draft_response() -> None:
    """Persist the raw LLM draft in ``draft_response``."""
    draft = "Consider validating the input string before converting it."
    with patch(
        "app.core.langgraph.nodes.hints.invoke_llm_with_retry",
        new=AsyncMock(return_value=draft),
    ):
        result: Dict[str, Any] = await generate_hint_node(_state())

    assert result["draft_response"] == draft


@pytest.mark.asyncio
async def test_hint_node_fallback_on_failure() -> None:
    """Return a fallback draft when the LLM call fails."""
    with patch(
        "app.core.langgraph.nodes.hints.invoke_llm_with_retry",
        new=AsyncMock(side_effect=Exception("LLM timed out")),
    ):
        result: Dict[str, Any] = await generate_hint_node(_state())

    assert "system disruption" in result["draft_response"].lower()


@pytest.mark.asyncio
async def test_hint_node_injects_revision_notice_on_retry() -> None:
    """Append the critical revision notice when regenerating after a rejection."""
    draft = "Break the problem down one step at a time."
    with patch(
        "app.core.langgraph.nodes.hints.invoke_llm_with_retry",
        new=AsyncMock(return_value=draft),
    ) as llm_mock:
        await generate_hint_node(
            GraphState(
                messages=[HumanMessage(content="How do I sort this list?")],
                outbound_trigger_reason="full_solution_leak",
            )
        )

    await_args = llm_mock.await_args
    assert await_args is not None
    system_prompt: str = await_args.args[0]
    assert "CRITICAL REVISION NOTICE" in system_prompt
    assert "full_solution_leak" in system_prompt


@pytest.mark.asyncio
async def test_hint_node_skips_when_inbound_blocked() -> None:
    """Return the constructive redirect instead of calling the LLM when inbound is blocked."""
    redirect = "Share your current attempt and I can help you improve it."
    with patch(
        "app.core.langgraph.nodes.hints.invoke_llm_with_retry",
        new=AsyncMock(),
    ) as llm_mock:
        result: Dict[str, Any] = await generate_hint_node(
            GraphState(
                messages=[HumanMessage(content="Give me the answer.")],
                is_safe_intent=False,
                constructive_redirect=redirect,
            )
        )

    assert result["draft_response"] == redirect
    llm_mock.assert_not_awaited()
