"""Outbound guardrail that evaluates assistant responses before delivery."""

from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts.guardrails import OUTBOUND_SYSTEM_PROMPT
from app.schemas.review import OutboundJudgeOutput, OutboundTriggerReason
from app.services.llm import llm_service

logger: Any = structlog.get_logger(__name__)

SAFE_TIMEOUT_RESPONSE = (
    "I couldn't complete a safety check just now. What is the smallest step you can try next to break the problem "
    "down?"
)


async def _invoke_outbound_judge(client: Any, messages: list[SystemMessage | HumanMessage]) -> OutboundJudgeOutput:
    """Invoke one client and validate its structured outbound decision."""
    if hasattr(client, "call"):
        response: Any = await client.call(messages, response_format=OutboundJudgeOutput)
    else:
        structured_client: Any = client.with_structured_output(OutboundJudgeOutput)
        response = await structured_client.ainvoke(messages)
    return response if isinstance(response, OutboundJudgeOutput) else OutboundJudgeOutput.model_validate(response)


async def outbound_node(
    state: dict[str, Any], primary_client: Any = None, fallback_client: Any = None
) -> dict[str, Any]:
    """Evaluate a draft response, retrying once with a fallback model."""
    raw_draft_response = state.get("draft_response", state.get("assistant_response", ""))
    raw_query = state.get("sanitized_query", "")
    raw_code = state.get("sanitized_code")
    draft_response = raw_draft_response if isinstance(raw_draft_response, str) else ""
    sanitized_query = raw_query if isinstance(raw_query, str) else ""
    sanitized_code = raw_code if isinstance(raw_code, str) and raw_code else None
    user_payload = f"Sanitized student query:\n{sanitized_query}\n\nAssistant draft response:\n{draft_response}"
    if sanitized_code is not None:
        user_payload = f"Sanitized student query:\n{sanitized_query}\n\nSanitized code:\n{sanitized_code}\n\nAssistant draft response:\n{draft_response}"

    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=OUTBOUND_SYSTEM_PROMPT),
        HumanMessage(content=user_payload),
    ]
    primary = primary_client or llm_service
    fallback = fallback_client or llm_service
    problem_id = state.get("problem_id")

    try:
        decision = await _invoke_outbound_judge(primary, messages)
        logger.info(
            "outbound_primary_completed",
            problem_id=problem_id,
            is_safe_output=decision.is_safe_output,
            outbound_trigger_reason=decision.outbound_trigger_reason,
        )
    except Exception as primary_error:
        logger.warning(
            "outbound_primary_failed_using_fallback",
            problem_id=problem_id,
            error_type=type(primary_error).__name__,
        )
        try:
            decision = await _invoke_outbound_judge(fallback, messages)
            logger.info(
                "outbound_fallback_completed",
                problem_id=problem_id,
                is_safe_output=decision.is_safe_output,
                outbound_trigger_reason=decision.outbound_trigger_reason,
            )
        except Exception as fallback_error:
            logger.exception(
                "outbound_fallback_failed_closed",
                problem_id=problem_id,
                error_type=type(fallback_error).__name__,
            )
            return {
                "is_safe_output": False,
                "outbound_trigger_reason": OutboundTriggerReason.EVALUATOR_ERROR,
                "constructive_redirect": None,
                "final_response": SAFE_TIMEOUT_RESPONSE,
            }

    final_response = draft_response if decision.is_safe_output else decision.constructive_redirect
    if not decision.is_safe_output:
        logger.info(
            "outbound_response_blocked",
            problem_id=problem_id,
            is_safe_output=decision.is_safe_output,
            outbound_trigger_reason=decision.outbound_trigger_reason,
        )
    return {
        "is_safe_output": decision.is_safe_output,
        "outbound_trigger_reason": decision.outbound_trigger_reason,
        "constructive_redirect": decision.constructive_redirect,
        "final_response": final_response,
    }
