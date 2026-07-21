"""Offline integration smoke test for sequential inbound guardrails."""

import asyncio
from typing import Any

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
        """Return a blocked intent decision without a network call."""
        return InboundIntentJudgeOutput(
            is_safe_intent=False,
            trigger_reason=InboundTriggerReason.SOLUTION_EXTRACTION,
            constructive_redirect="Share your current attempt and I can help you improve it.",
        )


class MockTimeoutJudgeClient:
    """A structured LLM mock that exceeds the intent-node timeout budgets."""

    def with_structured_output(self, _schema: type[InboundIntentJudgeOutput]) -> "MockTimeoutJudgeClient":
        """Return this mock as a structured-output runnable."""
        return self

    async def ainvoke(self, _messages: Any) -> InboundIntentJudgeOutput:
        """Sleep long enough for the node timeout wrapper to cancel the call."""
        await asyncio.sleep(3)
        return InboundIntentJudgeOutput(is_safe_intent=True)


async def run_pipeline(
    state: dict[str, Any], primary_client: Any, fallback_client: Any
) -> dict[str, Any]:
    """Run DLP first and judge sanitized input only when DLP passes."""
    dlp_update = await inbound_dlp_node(state)
    pipeline_state = {**state, **dlp_update}
    if not dlp_update["is_safe_sensitive"]:
        return pipeline_state

    intent_update = await inbound_intent_node(pipeline_state, primary_client, fallback_client)
    return {**pipeline_state, **intent_update}


async def main() -> None:
    """Exercise the four offline Stage 1 and Stage 2 smoke scenarios."""
    try:
        debug_result = await run_pipeline(
            {"user_query": "Why does this function raise an IndexError?", "code": "items[4]"},
            MockSuccessJudgeClient(),
            MockSuccessJudgeClient(),
        )
        assert debug_result["is_safe_sensitive"] is True
        assert debug_result["is_safe_intent"] is True
        print("Scenario 1 PASS: legitimate debug query passed both guardrail stages.")
    except AssertionError:
        print("Scenario 1 FAIL: legitimate debug query did not pass both guardrail stages.")
        raise

    try:
        leaked_secret = "sk-abcdefghijklmnopqrstuvwx"
        dlp_result = await run_pipeline(
            {"user_query": "Can you review this configuration?", "code": f'api_key = "{leaked_secret}"'},
            MockSuccessJudgeClient(),
            MockSuccessJudgeClient(),
        )
        assert dlp_result["is_safe_sensitive"] is False
        assert dlp_result["trigger_reason"] is InboundTriggerReason.SENSITIVE_DATA_EXPOSURE
        assert dlp_result["sanitized_code"] == 'api_key = "[REDACTED_SECRET]"'
        print("Scenario 2 PASS: Stage 1 blocked and redacted the hardcoded API key.")
    except AssertionError:
        print("Scenario 2 FAIL: Stage 1 did not block or redact the hardcoded API key.")
        raise

    try:
        extraction_result = await run_pipeline(
            {"user_query": "Give me a complete ready-to-paste solution for this assignment."},
            MockBlockJudgeClient(),
            MockSuccessJudgeClient(),
        )
        assert extraction_result["is_safe_sensitive"] is True
        assert extraction_result["is_safe_intent"] is False
        assert extraction_result["trigger_reason"] is InboundTriggerReason.SOLUTION_EXTRACTION
        print("Scenario 3 PASS: Stage 2 blocked the solution-extraction request.")
    except AssertionError:
        print("Scenario 3 FAIL: Stage 2 did not block the solution-extraction request.")
        raise

    try:
        timeout_result = await run_pipeline(
            {"user_query": "Please explain this loop."},
            MockTimeoutJudgeClient(),
            MockTimeoutJudgeClient(),
        )
        assert timeout_result["is_safe_sensitive"] is True
        assert timeout_result["is_safe_intent"] is False
        assert timeout_result["trigger_reason"] is InboundTriggerReason.EVALUATOR_ERROR
        print("Scenario 4 PASS: Stage 2 failed closed after both judge timeouts.")
    except AssertionError:
        print("Scenario 4 FAIL: Stage 2 did not fail closed after judge timeouts.")
        raise


if __name__ == "__main__":
    asyncio.run(main())
