"""Async offline tests for the outbound response guardrail."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.core.langgraph.nodes.outbound import SAFE_TIMEOUT_RESPONSE, outbound_node
from app.schemas import GraphState
from app.schemas.review import OutboundJudgeOutput, OutboundTriggerReason


def _state_with_draft(draft: str) -> GraphState:
    """Build a GraphState carrying a human query and an assistant draft."""
    return GraphState(
        messages=[
            HumanMessage(content="How should I approach this loop?"),
            AIMessage(content=draft),
        ]
    )


def _decision(
    is_safe: bool = True,
    reason: OutboundTriggerReason | None = None,
    redirect: str | None = None,
) -> OutboundJudgeOutput:
    """Build a canned outbound judge decision."""
    return OutboundJudgeOutput(
        is_safe_output=is_safe,
        outbound_trigger_reason=reason,
        constructive_redirect=redirect,
    )


@pytest.mark.asyncio
async def test_outbound_allows_conceptual_hint() -> None:
    """Allow conceptual guidance and deliver the original draft."""
    draft = "Start by deciding which invariant your loop should preserve."
    with patch(
        "app.core.langgraph.nodes.outbound._invoke_outbound_judge",
        new=AsyncMock(return_value=_decision(is_safe=True)),
    ):
        result = await outbound_node(_state_with_draft(draft))

    assert result.update["is_safe_output"] is True
    assert result.update["final_response"] == draft
    assert result.goto == "store_messages"


@pytest.mark.asyncio
async def test_outbound_blocks_full_code_leak() -> None:
    """Block a complete solution and deliver the constructive redirect."""
    with patch(
        "app.core.langgraph.nodes.outbound._invoke_outbound_judge",
        new=AsyncMock(
            return_value=_decision(
                is_safe=False,
                reason=OutboundTriggerReason.FULL_SOLUTION_LEAK,
                redirect="What helper function could you write first to handle one input at a time?",
            )
        ),
    ):
        result = await outbound_node(_state_with_draft("def complete_solution(): pass"))

    assert result.update["is_safe_output"] is False
    assert result.update["outbound_trigger_reason"] == OutboundTriggerReason.FULL_SOLUTION_LEAK
    assert result.update["final_response"] == result.update["constructive_redirect"]


@pytest.mark.asyncio
async def test_outbound_regenerates_below_max_attempts() -> None:
    """Route back to the agent while regeneration attempts remain."""
    state = GraphState(
        messages=[
            HumanMessage(content="Solve my assignment."),
            AIMessage(content="def complete_solution(): pass"),
        ],
        outbound_attempts=1,
    )
    with patch(
        "app.core.langgraph.nodes.outbound._invoke_outbound_judge",
        new=AsyncMock(return_value=_decision(is_safe=False, reason=OutboundTriggerReason.FULL_SOLUTION_LEAK)),
    ):
        result = await outbound_node(state)

    assert result.update["outbound_attempts"] == 2
    assert result.goto == "agent"


@pytest.mark.asyncio
async def test_outbound_fails_closed_on_client_failure() -> None:
    """Block the response when the outbound evaluator fails."""
    with patch(
        "app.core.langgraph.nodes.outbound._invoke_outbound_judge",
        new=AsyncMock(side_effect=RuntimeError("outbound judge unavailable")),
    ):
        result = await outbound_node(_state_with_draft("Try tracing the values after each iteration."))

    assert result.update["is_safe_output"] is False
    assert result.update["outbound_trigger_reason"] == OutboundTriggerReason.EVALUATOR_ERROR
    assert result.update["final_response"] == SAFE_TIMEOUT_RESPONSE
    assert result.goto == "store_messages"


@pytest.mark.asyncio
async def test_outbound_fails_closed_on_client_timeout() -> None:
    """Block the response when the outbound evaluator times out."""
    with patch(
        "app.core.langgraph.nodes.outbound._invoke_outbound_judge",
        new=AsyncMock(side_effect=asyncio.TimeoutError("outbound judge timed out")),
    ):
        result = await outbound_node(_state_with_draft("Try tracing the values after each iteration."))

    assert result.update["is_safe_output"] is False
    assert result.update["outbound_trigger_reason"] == OutboundTriggerReason.EVALUATOR_ERROR
    assert result.update["final_response"] == SAFE_TIMEOUT_RESPONSE
