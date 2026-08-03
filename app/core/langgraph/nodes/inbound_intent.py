"""Inbound guardrail stage 2: LLM intent judge (observability only).

Secret redaction happens in the earlier secret_guardrail_node stage, so the
query read here is already scrubbed. The intent decision never blocks the
mentoring workflow — it is retained for observability only.
"""

import asyncio
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.prompts.guardrails import INBOUND_INTENT_SYSTEM_PROMPT
from app.schemas import GraphState
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


async def inbound_intent_node(state: GraphState, primary_client: Any = None) -> dict[str, Any]:
    """Observe request intent via an LLM judge without ever blocking.

    Redaction is handled by the earlier secret_guardrail_node stage, so the
    query read here is already scrubbed. The intent decision is retained for
    observability only — it never blocks the mentoring workflow.
    """
    latest_human = next(
        (message for message in reversed(state.messages) if isinstance(message, HumanMessage)),
        None,
    )
    user_query = latest_human.content if latest_human and isinstance(latest_human.content, str) else ""

    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=INBOUND_INTENT_SYSTEM_PROMPT),
        HumanMessage(content=user_query),
    ]
    client = primary_client or llm_service
    problem_id = getattr(state, "problem_id", None)

    try:
        decision = await _invoke_intent_judge(client, messages, timeout=2.5)
        logger.info("inbound_intent_primary_completed", problem_id=problem_id, is_safe_intent=decision.is_safe_intent)
        return {
            "is_safe_intent": True,
            "inbound_trigger_reason": decision.inbound_trigger_reason,
            "constructive_redirect": decision.constructive_redirect,
        }
    except Exception as primary_error:
        error_type = type(primary_error).__name__
        logger.exception(
            "inbound_intent_evaluator_error",
            problem_id=problem_id,
            error_type=error_type,
        )
        return {
            "is_safe_intent": True,
            "inbound_trigger_reason": InboundTriggerReason.EVALUATOR_ERROR,
            "constructive_redirect": None,
        }
