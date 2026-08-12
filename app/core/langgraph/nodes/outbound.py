"""Outbound guardrail that gates assistant responses with a regenerate loop.

The agent's latest draft is judged against the user's query and the code the
agent retrieved via the search_code tool (which contains the student's own
uploaded code). Safe drafts are delivered; full-solution leaks route back to
the agent with a regenerate instruction, bounded by ``MAX_OUTBOUND_ATTEMPTS``.
Judge failures fail closed so an unchecked draft is never delivered.
"""

import asyncio
from typing import Any

import structlog
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import Command
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.observability import build_langfuse_config
from app.prompts.guardrails import OUTBOUND_SYSTEM_PROMPT
from app.schemas import GraphState
from app.schemas.review import OutboundJudgeOutput, OutboundTriggerReason
from app.services.llm import llm_service

logger: Any = structlog.get_logger(__name__)

MAX_OUTBOUND_ATTEMPTS = 4

MAX_RETRIEVED_CODE_CHARS = 15_000

REGENERATE_INSTRUCTION = (
    "Your previous response gave a complete, directly usable solution. That is not allowed. "
    "Rewrite your answer as guidance only: point out the relevant concept, the likely cause "
    "of the issue, or the general approach — without providing a full working implementation. "
    "Do not include a complete corrected version of the code."
)

SAFE_TIMEOUT_RESPONSE = (
    "I couldn't complete a safety check just now. What is the smallest step you can try next to break the problem "
    "down?"
)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=5),
    reraise=True,
)
async def _invoke_outbound_judge(
    model: Any,
    messages: list[SystemMessage | HumanMessage],
    timeout: float = 10.0,
    config: RunnableConfig | None = None,
) -> OutboundJudgeOutput:
    """Invoke one model and validate its structured outbound decision."""
    if hasattr(model, "call"):
        response: Any = await asyncio.wait_for(
            model.call(messages, response_format=OutboundJudgeOutput, config=config),
            timeout=timeout,
        )
    else:
        structured_client: Any = model.with_structured_output(OutboundJudgeOutput)
        response = await asyncio.wait_for(
            structured_client.ainvoke(messages, config=build_langfuse_config(config)),
            timeout=timeout,
        )
    return response if isinstance(response, OutboundJudgeOutput) else OutboundJudgeOutput.model_validate(response)


def _last_human_query(state: GraphState) -> str:
    """Return the content of the last HumanMessage in state, if any."""
    for message in reversed(state.messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def _last_ai_message_content(state: GraphState) -> str:
    """Return the content of the last non-empty AIMessage in state, if any."""
    for message in reversed(state.messages):
        if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
            return message.content
    return ""


def _collect_retrieved_code(state: GraphState) -> str:
    """Collect search_code tool results from the current turn's state."""
    sections: list[str] = []
    total = 0
    for message in state.messages:
        if not (isinstance(message, ToolMessage) and message.name == "search_code"):
            continue
        content = str(message.content or "")
        if not content:
            continue
        if total + len(content) > MAX_RETRIEVED_CODE_CHARS:
            content = content[: MAX_RETRIEVED_CODE_CHARS - total] + "\n...[truncated]"
        sections.append(content)
        total += len(content)
        if total >= MAX_RETRIEVED_CODE_CHARS:
            break
    return "\n---\n".join(sections)


async def outbound_node(state: GraphState, config: RunnableConfig | None = None) -> Command:
    """Judge the agent's latest draft and route it to delivery or regeneration.

    Reads the last HumanMessage, the last AIMessage, and the search_code
    ToolMessages straight from state. Returns a Command: store_messages on
    approval or exhausted attempts, agent for a bounded regenerate loop.
    """
    user_query = _last_human_query(state)
    draft = _last_ai_message_content(state)

    if not draft:
        logger.warning("outbound_missing_draft")
        return Command(
            update={
                "is_safe_output": False,
                "outbound_trigger_reason": OutboundTriggerReason.EVALUATOR_ERROR,
                "constructive_redirect": None,
                "final_response": SAFE_TIMEOUT_RESPONSE,
            },
            goto="store_messages",
        )

    retrieved_code = _collect_retrieved_code(state)
    user_payload = f"User query:\n{user_query}\n\n"
    if retrieved_code:
        user_payload += f"Code the agent retrieved from the student's uploaded files:\n{retrieved_code}\n\n"
    user_payload += f"Assistant draft response:\n{draft}"

    messages: list[SystemMessage | HumanMessage] = [
        SystemMessage(content=OUTBOUND_SYSTEM_PROMPT),
        HumanMessage(content=user_payload),
    ]
    model = llm_service.get_llm()
    attempts_used = state.outbound_attempts + 1

    try:
        decision = await _invoke_outbound_judge(model, messages, config=config)
        logger.info(
            "outbound_primary_completed",
            is_safe_output=decision.is_safe_output,
            attempts_used=attempts_used,
        )
        if decision.is_safe_output:
            return Command(
                update={
                    "is_safe_output": True,
                    "outbound_trigger_reason": None,
                    "constructive_redirect": None,
                    "final_response": draft,
                },
                goto="store_messages",
            )
        if attempts_used < MAX_OUTBOUND_ATTEMPTS:
            logger.info(
                "outbound_regenerating",
                attempts_used=attempts_used,
                outbound_trigger_reason=decision.outbound_trigger_reason,
            )
            return Command(
                update={
                    "is_safe_output": False,
                    "outbound_trigger_reason": decision.outbound_trigger_reason,
                    "constructive_redirect": decision.constructive_redirect,
                    "outbound_attempts": state.outbound_attempts + 1,
                    "messages": [SystemMessage(content=REGENERATE_INSTRUCTION)],
                },
                goto="agent",
            )
        redirect = decision.constructive_redirect or SAFE_TIMEOUT_RESPONSE
        logger.info("outbound_max_attempts_reached", attempts_used=attempts_used)
        return Command(
            update={
                "is_safe_output": False,
                "outbound_trigger_reason": decision.outbound_trigger_reason,
                "constructive_redirect": decision.constructive_redirect,
                "final_response": redirect,
            },
            goto="store_messages",
        )
    except Exception as primary_error:
        error_type = type(primary_error).__name__
        logger.exception("outbound_evaluator_error", error_type=error_type)
        return Command(
            update={
                "is_safe_output": False,
                "outbound_trigger_reason": OutboundTriggerReason.EVALUATOR_ERROR,
                "constructive_redirect": None,
                "final_response": SAFE_TIMEOUT_RESPONSE,
            },
            goto="store_messages",
        )
