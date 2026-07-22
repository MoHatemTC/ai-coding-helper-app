"""LangGraph node for storing messages to the messages table."""

import asyncio
from typing import Any

from app.core.logging import logger
from app.services.message import message_service


async def store_messages_node(state: dict[str, Any]) -> dict[str, Any]:
    """Store the current turn's messages to the messages table.

    Runs as a background task after the LLM response and guardrails.
    Extracts user and assistant messages from the graph state and
    batch-stores them to the messages table.

    Args:
        state: The current graph state containing messages.

    Returns:
        Empty dict — this node doesn't modify state.
    """
    messages = state.get("messages", [])
    metadata = state.get("_metadata", {})
    user_id = metadata.get("user_id")
    session_id = metadata.get("session_id")

    if not user_id or not session_id or not messages:
        return {}

    storage_messages = []
    for msg in messages:
        role = getattr(msg, "type", None) or msg.get("role", "") if isinstance(msg, dict) else ""
        content = getattr(msg, "content", None) or msg.get("content", "") if isinstance(msg, dict) else ""

        if not content:
            continue

        storage_messages.append({"role": role, "content": str(content)})

    if not storage_messages:
        return {}

    logger.info(
        "store_messages_node_triggered",
        session_id=session_id,
        message_count=len(storage_messages),
    )

    asyncio.create_task(
        message_service.store_messages(
            user_id=int(user_id),
            session_id=session_id,
            messages=storage_messages,
        )
    )

    return {}
