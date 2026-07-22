"""LangGraph node for summarizing older conversation context."""

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.logging import logger
from app.services.llm import llm_service
from app.utils.graph import _count_tokens_tiktoken

SUMMARIZATION_PROMPT = """You are a conversation summarizer. Summarize the following conversation messages into a concise narrative that preserves key context, decisions, and topics discussed.

Rules:
- Keep the summary under 200 words
- Preserve important facts, decisions, and code-related context
- Use present tense for readability
- Do not include filler or greetings
- Focus on substantive content only"""


async def summarization_node(state: dict[str, Any]) -> dict[str, Any]:
    """Summarize older messages when token count exceeds budget.

    Checks the total token count of messages against MAX_TOKENS.
    If exceeded, summarizes the oldest messages and stores the result
    in the 'summary' field of GraphState.

    Args:
        state: The current graph state containing messages and summary.

    Returns:
        Dict with 'summary' field updated if summarization was triggered.
    """
    messages = state.get("messages", [])
    existing_summary = state.get("summary", "")

    if not messages:
        return {}

    total_tokens = _count_tokens_tiktoken(messages)

    if total_tokens <= settings.MAX_TOKENS:
        return {}

    logger.info(
        "summarization_triggered",
        total_tokens=total_tokens,
        max_tokens=settings.MAX_TOKENS,
        message_count=len(messages),
    )

    half = len(messages) // 2
    older_messages = messages[:half]

    summary_input = []
    if existing_summary:
        summary_input.append(SystemMessage(content=f"Previous summary:\n{existing_summary}"))

    for msg in older_messages:
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            content = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
        if content:
            role = getattr(msg, "type", "unknown")
            summary_input.append(HumanMessage(content=f"{role}: {content}"))

    if not summary_input:
        return {}

    try:
        summary_response = await llm_service.call(
            [
                SystemMessage(content=SUMMARIZATION_PROMPT),
                *summary_input,
            ],
            temperature=0,
        )

        new_summary = summary_response.content if hasattr(summary_response, "content") else str(summary_response)

        if existing_summary:
            new_summary = f"{existing_summary}\n\n{new_summary}"

        logger.info(
            "summarization_completed",
            new_summary_length=len(new_summary),
        )

        return {"summary": new_summary}

    except Exception:
        logger.exception("summarization_failed")
        return {}
