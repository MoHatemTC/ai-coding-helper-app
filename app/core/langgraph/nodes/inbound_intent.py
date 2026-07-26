"""Inbound intent guardrail that evaluates sanitized user requests."""

from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts.guardrails import INBOUND_INTENT_SYSTEM_PROMPT
from app.schemas.review import InboundIntentJudgeOutput, InboundTriggerReason
from app.services.llm import llm_service

logger: Any = structlog.get_logger(__name__)


async def _invoke_intent_judge(client: Any, messages: list[SystemMessage | HumanMessage]) -> InboundIntentJudgeOutput:
    """Invoke one client and validate its structured intent decision."""
    if hasattr(client, "call"):
        response: Any = await client.call(messages, response_format=InboundIntentJudgeOutput)
    else:
        structured_client: Any = client.with_structured_output(InboundIntentJudgeOutput)
        response = await structured_client.ainvoke(messages)
    return (
        response
        if isinstance(response, InboundIntentJudgeOutput)
        else InboundIntentJudgeOutput.model_validate(response)
    )


async def inbound_intent_node(
    state: dict[str, Any], primary_client: Any = None, fallback_client: Any = None
) -> dict[str, Any]:
    """Classify sanitized inbound intent, retrying once with a fallback model."""
    raw_query = state.get("sanitized_query", "")
    raw_code = state.get("sanitized_code")
    sanitized_query = raw_query if isinstance(raw_query, str) else ""
    sanitized_code = raw_code if isinstance(raw_code, str) and raw_code else None
    user_payload = f"Sanitized user query:\n{sanitized_query}"
    if sanitized_code is not None:
        user_payload = f"{user_payload}\n\nSanitized code:\n{sanitized_code}"

    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=INBOUND_INTENT_SYSTEM_PROMPT),
        HumanMessage(content=user_payload),
    ]
    primary = primary_client or llm_service
    fallback = fallback_client or llm_service
    problem_id = state.get("problem_id")

    try:
        decision = await _invoke_intent_judge(primary, messages)
        logger.info("inbound_intent_primary_completed", problem_id=problem_id, is_safe_intent=decision.is_safe_intent)
    except Exception as primary_error:
        logger.warning(
            "inbound_intent_primary_failed_using_fallback",
            problem_id=problem_id,
            error_type=type(primary_error).__name__,
        )
        try:
            decision = await _invoke_intent_judge(fallback, messages)
            logger.info(
                "inbound_intent_fallback_completed", problem_id=problem_id, is_safe_intent=decision.is_safe_intent
            )
        except Exception as fallback_error:
            logger.exception(
                "inbound_intent_fallback_failed_closed",
                problem_id=problem_id,
                error_type=type(fallback_error).__name__,
            )
            return {
                "is_safe_intent": False,
                "inbound_trigger_reason": InboundTriggerReason.EVALUATOR_ERROR,
                "constructive_redirect": None,
            }

    return {
        "is_safe_intent": decision.is_safe_intent,
        "inbound_trigger_reason": decision.trigger_reason,
        "constructive_redirect": decision.constructive_redirect,
    }
