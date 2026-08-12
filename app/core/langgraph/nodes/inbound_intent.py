"""Inbound guardrail stage 2: LLM intent judge that gates the workflow.

Secret redaction happens in the earlier secret_guardrail_node stage, so the
query and code read here are already scrubbed. This node classifies the
request as safe, blocked, or un-judgeable and routes accordingly: safe
requests continue to the document pipeline, while blocked requests and judge
failures route to store_messages so the agent never sees the input.
"""

import asyncio
from pathlib import Path
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import Command
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.observability import build_langfuse_config
from app.prompts.guardrails import INBOUND_INTENT_SYSTEM_PROMPT
from app.schemas import GraphState
from app.schemas.review import InboundIntentJudgeOutput, InboundTriggerReason
from app.services.llm import llm_service

logger: Any = structlog.get_logger(__name__)

MAX_CODE_CHARS_PER_FILE = 15_000

GENERIC_REDIRECT = (
    "I can't process that request right now. Please rephrase your question so I can help you work through it."
)

INBOUND_REDIRECTS: dict[InboundTriggerReason, str] = {
    InboundTriggerReason.SOLUTION_EXTRACTION: (
        "I can guide you toward the solution, but I won't hand over a "
        "complete implementation. Let's break the problem down — tell me "
        "what you've tried so far and where you're stuck."
    ),
    InboundTriggerReason.OFF_TOPIC: (
        "That's outside my scope as your coding mentor. I can help with "
        "computer science, software engineering, and tech careers."
    ),
    InboundTriggerReason.HARMFUL_ILLEGAL: (
        "I can't help with that request. If you're working on a security "
        "topic, I'm glad to help you understand the underlying concepts in "
        "a safe, educational way."
    ),
}


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    reraise=True,
)
async def _invoke_intent_judge(
    model: Any,
    messages: list[SystemMessage | HumanMessage],
    timeout: float = 60.0,
    config: RunnableConfig | None = None,
) -> InboundIntentJudgeOutput:
    """Invoke one client and validate its structured intent decision."""
    if hasattr(model, "call"):
        response: Any = await asyncio.wait_for(
            model.call(messages, response_format=InboundIntentJudgeOutput, config=config),
            timeout=timeout,
        )
    else:
        structured_client: Any = model.with_structured_output(InboundIntentJudgeOutput)
        response = await asyncio.wait_for(
            structured_client.ainvoke(messages, config=build_langfuse_config(config)),
            timeout=timeout,
        )
    return (
        response
        if isinstance(response, InboundIntentJudgeOutput)
        else InboundIntentJudgeOutput.model_validate(response)
    )


async def _read_redacted_code(state: GraphState) -> str:
    """Read uploaded code (already redacted in stage 1) into a judge payload."""
    sections: list[str] = []
    for attachment in state.pending_files:
        try:
            content: str = await asyncio.to_thread(Path(attachment.stored_path).read_text, encoding="utf-8")
        except Exception:
            logger.exception(
                "inbound_intent_code_read_failed",
                file_id=attachment.file_id,
                file_name=attachment.original_name,
            )
            continue
        if len(content) > MAX_CODE_CHARS_PER_FILE:
            content = content[:MAX_CODE_CHARS_PER_FILE] + "\n...[truncated]"
        sections.append(f"--- {attachment.original_name} ({attachment.language}) ---\n{content}")
    return "\n\n".join(sections)


async def inbound_intent_node(state: GraphState, config: RunnableConfig | None = None) -> Command:
    """Judge request intent and gate the mentoring workflow.

    The query and any uploaded code are already redacted by stage 1. Safe
    requests continue to the document pipeline; blocked requests and judge
    failures route to store_messages so the agent never sees the input.
    """
    latest_human = next(
        (message for message in reversed(state.messages) if isinstance(message, HumanMessage)),
        None,
    )
    user_query = latest_human.content if latest_human and isinstance(latest_human.content, str) else ""

    uploaded_code = await _read_redacted_code(state)
    user_payload = f"User query:\n{user_query}"
    if uploaded_code:
        user_payload += f"\n\nUploaded code:\n{uploaded_code}"

    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=INBOUND_INTENT_SYSTEM_PROMPT),
        HumanMessage(content=user_payload),
    ]
    model = llm_service

    try:
        decision = await _invoke_intent_judge(model, messages, config=config)
        logger.info("inbound_intent_primary_completed", is_safe_intent=decision.is_safe_intent)
        if decision.is_safe_intent:
            return Command(
                update={
                    "is_safe_intent": True,
                    "inbound_trigger_reason": None,
                    "constructive_redirect": None,
                },
                goto="document_pipeline",
            )
        redirect = INBOUND_REDIRECTS.get(decision.inbound_trigger_reason) if decision.inbound_trigger_reason else None
        if not redirect:
            redirect = decision.constructive_redirect or GENERIC_REDIRECT
        return Command(
            update={
                "is_safe_intent": False,
                "inbound_trigger_reason": decision.inbound_trigger_reason,
                "constructive_redirect": redirect,
                "messages": [AIMessage(content=redirect)],
                "final_response": redirect,
            },
            goto="store_messages",
        )
    except Exception as primary_error:
        error_type = type(primary_error).__name__
        logger.exception("inbound_intent_evaluator_error", error_type=error_type)
        return Command(
            update={
                "is_safe_intent": False,
                "inbound_trigger_reason": InboundTriggerReason.EVALUATOR_ERROR,
                "constructive_redirect": GENERIC_REDIRECT,
                "messages": [AIMessage(content=GENERIC_REDIRECT)],
                "final_response": GENERIC_REDIRECT,
            },
            goto="store_messages",
        )
