"""Reusable LLM guardrail judges for the hint pipeline.

These wrap the existing structured-output judge prompts and LLM service so
both the graph node wrappers and any callers get traced, fail-closed
decisions without duplicating prompt/schema definitions.
"""

from typing import Any

import structlog
from langchain_core.callbacks import BaseCallbackManager
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.observability import langfuse_callback_handler
from app.prompts.guardrails import INBOUND_INTENT_SYSTEM_PROMPT, OUTBOUND_SYSTEM_PROMPT
from app.schemas.review import InboundIntentJudgeOutput, OutboundJudgeOutput
from app.services.llm import llm_service

logger: Any = structlog.get_logger(__name__)


def _build_invocation_config(config: RunnableConfig | None) -> RunnableConfig:
    """Merge langfuse tracing callbacks from the graph config into the call safely."""
    raw_callbacks = (config or {}).get("callbacks")

    if isinstance(raw_callbacks, BaseCallbackManager):
        callbacks: list = list(raw_callbacks.handlers)
    elif isinstance(raw_callbacks, list):
        callbacks = list(raw_callbacks)
    elif raw_callbacks is not None:
        callbacks = [raw_callbacks]
    else:
        callbacks = []

    if settings.LANGFUSE_TRACING_ENABLED and langfuse_callback_handler not in callbacks:
        callbacks.append(langfuse_callback_handler)

    return {"callbacks": callbacks} if callbacks else {}


def _validate(decision: Any, schema: type[InboundIntentJudgeOutput] | type[OutboundJudgeOutput]) -> Any:
    """Return a validated judge output, parsing raw dicts when necessary."""
    return decision if isinstance(decision, schema) else schema.model_validate(decision)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    reraise=True,
)
async def check_inbound_guardrails(
    user_query: str,
    config: RunnableConfig | None = None,
) -> InboundIntentJudgeOutput:
    """Judge whether the user query may reach the hint pipeline.

    Args:
        user_query: The redacted user query to classify.
        config: Runnable config carrying langfuse tracing callbacks.

    Returns:
        An ``InboundIntentJudgeOutput`` decision.

    Raises:
        Exception: When the judge fails after retries; callers fail closed.
    """
    structured = llm_service.get_llm().with_structured_output(InboundIntentJudgeOutput)
    decision = await structured.ainvoke(
        [
            SystemMessage(content=INBOUND_INTENT_SYSTEM_PROMPT),
            HumanMessage(content=f"User query:\n{user_query}"),
        ],
        config=_build_invocation_config(config),
    )
    logger.info("inbound_guardrail_completed", is_safe_intent=getattr(decision, "is_safe_intent", None))
    return _validate(decision, InboundIntentJudgeOutput)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    reraise=True,
)
async def check_outbound_guardrails(
    draft_response: str,
    user_query: str,
    code_context: str = "",
    config: RunnableConfig | None = None,
) -> OutboundJudgeOutput:
    """Judge whether the assistant draft response is safe to deliver.

    Args:
        draft_response: The hint pipeline's draft answer to judge.
        user_query: The user query the draft answers.
        code_context: Retrieved code chunks from the student's uploaded files.
        config: Runnable config carrying langfuse tracing callbacks.

    Returns:
        An ``OutboundJudgeOutput`` decision.

    Raises:
        Exception: When the judge fails after retries; callers fail closed.
    """
    payload = f"User query:\n{user_query}\n\n"
    if code_context:
        payload += f"Code the agent retrieved from the student's uploaded files:\n{code_context}\n\n"
    payload += f"Assistant draft response:\n{draft_response}"

    structured = llm_service.get_llm().with_structured_output(OutboundJudgeOutput)
    decision = await structured.ainvoke(
        [
            SystemMessage(content=OUTBOUND_SYSTEM_PROMPT),
            HumanMessage(content=payload),
        ],
        config=_build_invocation_config(config),
    )
    logger.info("outbound_guardrail_completed", is_safe_output=getattr(decision, "is_safe_output", None))
    return _validate(decision, OutboundJudgeOutput)
