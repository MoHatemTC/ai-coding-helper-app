"""Async offline tests for the outbound response guardrail."""

from typing import Any
import pytest
from app.core.langgraph.nodes.outbound import SAFE_TIMEOUT_RESPONSE, outbound_node
from app.schemas.review import OutboundJudgeOutput, OutboundTriggerReason


class MockSuccessJudgeClient:
    """A structured LLM mock that allows the assistant response."""

    def with_structured_output(self, _schema: type[OutboundJudgeOutput]) -> "MockSuccessJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> OutboundJudgeOutput:
        """Return a safe output decision without a network call."""
        return OutboundJudgeOutput(is_safe_output=True)


class MockBlockJudgeClient:
    """A structured LLM mock that blocks a full solution leak."""

    def with_structured_output(self, _schema: type[OutboundJudgeOutput]) -> "MockBlockJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> OutboundJudgeOutput:
        """Return a full-solution-leak decision without a network call."""
        return OutboundJudgeOutput(
            is_safe_output=False,
            trigger_reason=OutboundTriggerReason.FULL_SOLUTION_LEAK,
            constructive_redirect="What helper function could you write first to handle one input at a time?",
        )


class MockFailingJudgeClient:
    """A structured LLM mock that raises an evaluator error."""

    def with_structured_output(self, _schema: type[OutboundJudgeOutput]) -> "MockFailingJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> OutboundJudgeOutput:
        """Raise an error so the node exercises its fallback client."""
        raise RuntimeError("outbound judge unavailable")


@pytest.mark.asyncio
async def test_outbound_allows_conceptual_hint() -> None:
    """Allow conceptual guidance and deliver the original draft."""
    draft_response = "Start by deciding which invariant your loop should preserve."
    result = await outbound_node(
        {"sanitized_query": "How should I approach this loop?", "draft_response": draft_response},
        MockSuccessJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_output"] is True
    assert result["final_response"] == draft_response


@pytest.mark.asyncio
async def test_outbound_blocks_full_code_leak() -> None:
    """Block a complete solution and deliver the constructive redirect."""
    result = await outbound_node(
        {"sanitized_query": "Solve my assignment.", "draft_response": "def complete_solution(): pass"},
        MockBlockJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_output"] is False
    assert result["trigger_reason"] == OutboundTriggerReason.FULL_SOLUTION_LEAK
    assert result["final_response"] == result["constructive_redirect"]


@pytest.mark.asyncio
async def test_outbound_uses_fallback_client_on_primary_failure() -> None:
    """Allow the response when the fallback evaluator succeeds."""
    result = await outbound_node(
        {"draft_response": "Try tracing the values after each iteration."},
        MockFailingJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_output"] is True


@pytest.mark.asyncio
async def test_outbound_fails_closed_when_both_clients_fail() -> None:
    """Block the response when neither outbound evaluator is available."""
    result = await outbound_node(
        {"draft_response": "Try tracing the values after each iteration."},
        MockFailingJudgeClient(),
        MockFailingJudgeClient(),
    )

    assert result["is_safe_output"] is False
    assert result["trigger_reason"] == OutboundTriggerReason.EVALUATOR_ERROR
    assert result["final_response"] == SAFE_TIMEOUT_RESPONSE
