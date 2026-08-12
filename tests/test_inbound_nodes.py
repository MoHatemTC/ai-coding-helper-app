"""Async tests for sequential inbound secret redaction and intent guardrails."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.core.langgraph.nodes.inbound_first_stage import secret_guardrail_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.schemas import GraphState
from app.schemas.review import InboundIntentJudgeOutput, InboundTriggerReason


def _state_with_query(query: str) -> GraphState:
    """Build a GraphState carrying a single human message."""
    return GraphState(messages=[HumanMessage(content=query)])


def _decision(
    is_safe: bool = True,
    reason: InboundTriggerReason | None = None,
    redirect: str | None = None,
) -> InboundIntentJudgeOutput:
    """Build a canned inbound intent decision."""
    return InboundIntentJudgeOutput(
        is_safe_intent=is_safe,
        inbound_trigger_reason=reason,
        constructive_redirect=redirect,
    )


@pytest.mark.asyncio
async def test_secret_guardrail_redacts_api_key() -> None:
    """Redact a hardcoded API key from the prompt."""
    query = 'set OPENAI_API_KEY="sk-abcdefghijklmnopqrstuvwx"'  # pragma: allowlist secret
    result: Any = await secret_guardrail_node(_state_with_query(query))

    assert "messages" in result
    redacted = result["messages"][0].content
    assert "sk-abcdefghijklmnopqrstuvwx" not in redacted  # pragma: allowlist secret
    assert "[REDACTED_OPENAI_KEY]" in redacted


@pytest.mark.asyncio
async def test_secret_guardrail_leaves_placeholder_alone() -> None:
    """Ignore credential-like text in Python comment lines."""
    query = '    # api_key = "placeholder"\nprint("safe")'  # pragma: allowlist secret
    result: Any = await secret_guardrail_node(_state_with_query(query))

    assert result == {}


@pytest.mark.asyncio
async def test_secret_guardrail_ignores_python_identifiers() -> None:
    """Allow long Python identifiers that are not secrets."""
    query = "def process_financial_ledger(file_path: str):\n    return file_path\n"
    result: Any = await secret_guardrail_node(_state_with_query(query))

    assert result == {}


@pytest.mark.asyncio
async def test_secret_guardrail_redacts_high_entropy_secret() -> None:
    """Redact a 20+ char hex string exceeding the whitelist length cap."""
    query = 'secret_key = "a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4"'  # pragma: allowlist secret
    result: Any = await secret_guardrail_node(_state_with_query(query))

    assert "messages" in result
    assert "a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4" not in result["messages"][0].content  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_intent_allows_debug_request() -> None:
    """Allow a standard debugging request."""
    with patch(
        "app.core.langgraph.nodes.inbound_intent._invoke_intent_judge",
        new=AsyncMock(return_value=_decision(is_safe=True)),
    ):
        result = await inbound_intent_node(_state_with_query("Why does this function raise an IndexError?"))

    assert result.update["is_safe_intent"] is True
    assert result.goto == "document_pipeline"


@pytest.mark.asyncio
async def test_intent_blocks_solution_extraction() -> None:
    """Block requests for a ready-to-submit solution."""
    with patch(
        "app.core.langgraph.nodes.inbound_intent._invoke_intent_judge",
        new=AsyncMock(
            return_value=_decision(
                is_safe=False,
                reason=InboundTriggerReason.SOLUTION_EXTRACTION,
                redirect="Share your current attempt and I can help you improve it.",
            )
        ),
    ):
        result = await inbound_intent_node(_state_with_query("Give me a complete ready-to-paste solution."))

    assert result.update["is_safe_intent"] is False
    assert result.update["inbound_trigger_reason"] == InboundTriggerReason.SOLUTION_EXTRACTION
    assert result.update["constructive_redirect"]
    assert result.goto == "store_messages"


@pytest.mark.asyncio
async def test_intent_blocks_off_topic_query() -> None:
    """Block prompts outside the coding mentor's scope."""
    with patch(
        "app.core.langgraph.nodes.inbound_intent._invoke_intent_judge",
        new=AsyncMock(
            return_value=_decision(
                is_safe=False,
                reason=InboundTriggerReason.OFF_TOPIC,
                redirect="Please keep your question focused on software engineering.",
            )
        ),
    ):
        result = await inbound_intent_node(_state_with_query("Write an essay about ancient Roman architecture."))

    assert result.update["is_safe_intent"] is False
    assert result.update["inbound_trigger_reason"] == InboundTriggerReason.OFF_TOPIC


@pytest.mark.asyncio
async def test_intent_fails_closed_on_client_failure() -> None:
    """Block the request when the intent evaluator fails."""
    with patch(
        "app.core.langgraph.nodes.inbound_intent._invoke_intent_judge",
        new=AsyncMock(side_effect=RuntimeError("intent judge unavailable")),
    ):
        result = await inbound_intent_node(_state_with_query("Please explain this loop."))

    assert result.update["is_safe_intent"] is False
    assert result.update["inbound_trigger_reason"] == InboundTriggerReason.EVALUATOR_ERROR
    assert result.goto == "store_messages"


@pytest.mark.asyncio
async def test_intent_fails_closed_on_client_timeout() -> None:
    """Block the request when the intent evaluator times out."""
    with patch(
        "app.core.langgraph.nodes.inbound_intent._invoke_intent_judge",
        new=AsyncMock(side_effect=asyncio.TimeoutError("intent judge timed out")),
    ):
        result = await inbound_intent_node(_state_with_query("Please explain this loop."))

    assert result.update["is_safe_intent"] is False
    assert result.update["inbound_trigger_reason"] == InboundTriggerReason.EVALUATOR_ERROR
