"""Async tests for sequential inbound DLP and intent guardrails."""

import asyncio
from typing import Any

import pytest

from app.core.langgraph.nodes.inbound_first_stage import inbound_dlp_node
from app.core.langgraph.nodes.inbound_intent import inbound_intent_node
from app.schemas.review import InboundIntentJudgeOutput, InboundTriggerReason


class MockSuccessJudgeClient:
    """A structured LLM mock that allows the inbound request."""

    def with_structured_output(self, _schema: type[InboundIntentJudgeOutput]) -> "MockSuccessJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> InboundIntentJudgeOutput:
        """Return a safe intent decision without a network call."""
        return InboundIntentJudgeOutput(is_safe_intent=True)


class MockBlockJudgeClient:
    """A structured LLM mock that blocks solution extraction."""

    def with_structured_output(self, _schema: type[InboundIntentJudgeOutput]) -> "MockBlockJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> InboundIntentJudgeOutput:
        """Return a solution-extraction decision without a network call."""
        return InboundIntentJudgeOutput(
            is_safe_intent=False,
            inbound_trigger_reason=InboundTriggerReason.SOLUTION_EXTRACTION,
            constructive_redirect="Share your current attempt and I can help you improve it.",
        )


class MockFailingJudgeClient:
    """A structured LLM mock that raises an evaluator error."""

    def with_structured_output(self, _schema: type[InboundIntentJudgeOutput]) -> "MockFailingJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> InboundIntentJudgeOutput:
        """Raise an error so the node exercises its fallback client."""
        raise RuntimeError("intent judge unavailable")


class MockTimeoutJudgeClient:
    """A structured LLM mock that hangs to trigger a timeout."""

    def with_structured_output(self, _schema: type[InboundIntentJudgeOutput]) -> "MockTimeoutJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> InboundIntentJudgeOutput:
        """Sleep longer than the timeout budget."""
        await asyncio.sleep(5.0)
        return InboundIntentJudgeOutput(is_safe_intent=True)


class MockOffTopicJudgeClient:
    """A structured LLM mock that blocks off-topic requests."""

    def with_structured_output(self, _schema: type[InboundIntentJudgeOutput]) -> "MockOffTopicJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> InboundIntentJudgeOutput:
        """Return an off-topic decision without a network call."""
        return InboundIntentJudgeOutput(
            is_safe_intent=False,
            inbound_trigger_reason=InboundTriggerReason.OFF_TOPIC,
            constructive_redirect="Please keep your question focused on software engineering.",
        )


async def run_pipeline(state: dict[str, Any], primary_client: Any, fallback_client: Any) -> dict[str, Any]:
    """Run DLP first and judge sanitized input only when DLP passes."""
    dlp_update = await inbound_dlp_node(state)
    pipeline_state = {**state, **dlp_update}
    if not dlp_update["is_safe_sensitive"]:
        return pipeline_state

    intent_update = await inbound_intent_node(pipeline_state, primary_client, fallback_client)
    return {**pipeline_state, **intent_update}


@pytest.mark.asyncio
async def test_legitimate_debug_request() -> None:
    """Allow a standard debugging request with a code snippet."""
    result = await run_pipeline(
        {"user_query": "Why does this function raise an IndexError?", "code": "items[4]"},
        MockSuccessJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_sensitive"] is True
    assert result["is_safe_intent"] is True


@pytest.mark.asyncio
async def test_dlp_commented_text_does_not_trigger() -> None:
    """Ignore credential-like text in Python comment lines."""
    result = await run_pipeline(
        {"user_query": "Can you review this code?", "code": '    # api_key = "placeholder"\nprint("safe")'},
        MockSuccessJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_sensitive"] is True


@pytest.mark.asyncio
async def test_dlp_python_identifiers_do_not_trigger_entropy() -> None:
    """Allow long Python identifiers that are not secrets."""
    result = await run_pipeline(
        {
            "user_query": "Can you review this code?",
            "code": "def process_financial_ledger(file_path: str):\n    return file_path\n",
        },
        MockSuccessJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_sensitive"] is True


@pytest.mark.asyncio
async def test_dlp_flags_long_high_entropy_secret_without_dashes() -> None:
    """Flag a 20+ char hex string exceeding whitelist length cap."""
    result = await run_pipeline(
        {
            "user_query": "Can you review this code?",
            "code": 'secret_key = "a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4"',
        },
        MockSuccessJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_sensitive"] is False
    assert "high_entropy_token" in result["detected_secret_types"]


@pytest.mark.asyncio
async def test_dlp_blocks_and_redacts_api_key() -> None:
    """Block and redact a real hardcoded API key."""
    result = await run_pipeline(
        {"user_query": "Can you review this configuration?", "code": 'api_key = "sk-abcdefghijklmnopqrstuvwx"'},
        MockSuccessJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_sensitive"] is False
    assert result["inbound_trigger_reason"] == InboundTriggerReason.SENSITIVE_DATA_EXPOSURE
    assert result["sanitized_code"] == 'api_key = "[REDACTED_SECRET]"'


@pytest.mark.asyncio
async def test_intent_blocks_solution_extraction() -> None:
    """Block requests for a ready-to-submit solution."""
    result = await run_pipeline(
        {"user_query": "Give me a complete ready-to-paste solution for this assignment."},
        MockBlockJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_intent"] is False
    assert result["inbound_trigger_reason"] == InboundTriggerReason.SOLUTION_EXTRACTION
    assert result["constructive_redirect"]


@pytest.mark.asyncio
async def test_intent_blocks_off_topic_query() -> None:
    """Block prompts outside the coding mentor's scope."""
    result = await run_pipeline(
        {"user_query": "Write an essay about ancient Roman architecture."},
        MockOffTopicJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_intent"] is False
    assert result["inbound_trigger_reason"] == InboundTriggerReason.OFF_TOPIC


@pytest.mark.asyncio
async def test_intent_uses_fallback_client_on_primary_failure() -> None:
    """Allow the request when the fallback evaluator succeeds."""
    result = await run_pipeline(
        {"user_query": "Please explain this loop."},
        MockFailingJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_intent"] is True


@pytest.mark.asyncio
async def test_intent_uses_fallback_client_on_primary_timeout() -> None:
    """Allow the request when primary times out but fallback evaluator succeeds."""
    result = await run_pipeline(
        {"user_query": "Please explain this loop."},
        MockTimeoutJudgeClient(),
        MockSuccessJudgeClient(),
    )

    assert result["is_safe_intent"] is True


@pytest.mark.asyncio
async def test_intent_fails_closed_when_both_clients_fail() -> None:
    """Block the request when neither intent evaluator is available."""
    result = await run_pipeline(
        {"user_query": "Please explain this loop."},
        MockFailingJudgeClient(),
        MockFailingJudgeClient(),
    )

    assert result["is_safe_intent"] is False
    assert result["inbound_trigger_reason"] == InboundTriggerReason.EVALUATOR_ERROR


@pytest.mark.asyncio
async def test_intent_fails_closed_when_both_clients_timeout() -> None:
    """Block the request when both intent evaluators time out."""
    result = await run_pipeline(
        {"user_query": "Please explain this loop."},
        MockTimeoutJudgeClient(),
        MockTimeoutJudgeClient(),
    )

    assert result["is_safe_intent"] is False
    assert result["inbound_trigger_reason"] == InboundTriggerReason.EVALUATOR_ERROR

