"""Inbound guardrail that redacts secrets and observes request intent."""

import asyncio
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.langgraph.nodes.inbound_first_stage import _scan_and_sanitize
from app.prompts.guardrails import INBOUND_INTENT_SYSTEM_PROMPT
from app.schemas.review import InboundIntentJudgeOutput, InboundTriggerReason
from app.services.llm import llm_service

logger: Any = structlog.get_logger(__name__)


async def _invoke_intent_judge(
    client: Any, messages: list[SystemMessage | HumanMessage], timeout: float = 2.5
) -> InboundIntentJudgeOutput:
    """Invoke one client and validate its structured intent decision."""
    if hasattr(client, "call"):
        response: Any = await asyncio.wait_for(
            client.call(messages, response_format=InboundIntentJudgeOutput),
            timeout=timeout,
        )
    else:
        structured_client: Any = client.with_structured_output(InboundIntentJudgeOutput)
        response = await asyncio.wait_for(
            structured_client.ainvoke(messages),
            timeout=timeout,
        )
    return (
        response
        if isinstance(response, InboundIntentJudgeOutput)
        else InboundIntentJudgeOutput.model_validate(response)
    )


async def inbound_intent_node(state: dict[str, Any], primary_client: Any = None) -> dict[str, Any]:
    """Redact inbound secrets while allowing every request to continue.

    The intent decision is retained for observability, but it deliberately
    never blocks the mentoring workflow.  Secret scanning lives here rather
    than as a separate graph stage so the agent only receives redacted text.
    """
    raw_query = state.get("user_query")
    if not isinstance(raw_query, str):
        latest_human = next(
            (message for message in reversed(state.get("messages", [])) if isinstance(message, HumanMessage)),
            None,
        )
        raw_query = latest_human.content if latest_human and isinstance(latest_human.content, str) else ""
    raw_code = state.get("code")
    user_query = raw_query if isinstance(raw_query, str) else ""
    code = raw_code if isinstance(raw_code, str) and raw_code else None
    sanitized_query, query_secret_types = _scan_and_sanitize(user_query)
    sanitized_code, code_secret_types = _scan_and_sanitize(code) if code else (None, [])
    has_secret = bool(query_secret_types or code_secret_types)

    user_payload = f"User query:\n{sanitized_query}"
    if sanitized_code is not None:
        user_payload = f"{user_payload}\n\nCode:\n{sanitized_code}"

    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=INBOUND_INTENT_SYSTEM_PROMPT),
        HumanMessage(content=user_payload),
    ]
    client = primary_client or llm_service
    problem_id = state.get("problem_id")

    try:
        decision = await _invoke_intent_judge(client, messages, timeout=2.5)
        logger.info("inbound_intent_primary_completed", problem_id=problem_id, is_safe_intent=decision.is_safe_intent)
    except (asyncio.TimeoutError, Exception) as primary_error:
        error_type = (
            "TimeoutError" if isinstance(primary_error, asyncio.TimeoutError) else type(primary_error).__name__
        )
        logger.exception(
            "inbound_intent_failed_closed",
            problem_id=problem_id,
            error_type=error_type,
        )
        update: dict[str, Any] = {
            "is_safe_intent": True,
            "inbound_trigger_reason": InboundTriggerReason.EVALUATOR_ERROR,
            "constructive_redirect": None,
        }
        return _redaction_update(state, sanitized_query, has_secret, update)

    update = {
        "is_safe_intent": True,
        "inbound_trigger_reason": decision.inbound_trigger_reason,
        "constructive_redirect": decision.constructive_redirect,
    }
    return _redaction_update(state, sanitized_query, has_secret, update)


def _redaction_update(
    state: dict[str, Any], sanitized_query: str, has_secret: bool, update: dict[str, Any]
) -> dict[str, Any]:
    """Overwrite the current human message with the redacted version."""
    if not has_secret:
        update["user_query_redacted"] = False
        return update

    human_message = next(
        (message for message in reversed(state.get("messages", [])) if isinstance(message, HumanMessage)),
        None,
    )
    if human_message is None:
        update["user_query_redacted"] = True
        return update

    redaction_notice = (
        "⚠️ A secret or credential was detected in your message and has been removed. "
        "Please never share API keys, passwords, or tokens.\n\n"
    )
    update.update(
        {
            "user_query_redacted": True,
            "messages": [HumanMessage(content=redaction_notice + sanitized_query, id=human_message.id)],
        }
    )
    return update
