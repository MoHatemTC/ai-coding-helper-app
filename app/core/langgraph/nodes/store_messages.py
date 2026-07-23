"""LangGraph node for storing messages to mem0 and messages table."""

import asyncio
from typing import cast

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    convert_to_openai_messages,
)

from app.core.logging import logger
from app.schemas import GraphState
from app.services.memory import memory_service
from app.services.message import message_service


async def store_messages_node(state: GraphState) -> dict:
    """Store the current turn's messages to mem0 and messages table.

    Runs after the LLM response completes (no more tool calls).
    Uses _last_message_index to identify new messages from this turn.

    Args:
        state: The current graph state containing messages and _last_message_index.

    Returns:
        Empty dict — this node doesn't modify state.
    """
    messages = state.messages
    last_message_index = state._last_message_index
    metadata = getattr(state, "_metadata", {})
    user_id = metadata.get("user_id")
    session_id = metadata.get("session_id")

    if not user_id or not session_id or not messages:
        return {}

    # Get new messages from this turn using _last_message_index
    new_messages = messages[last_message_index:]

    if not new_messages:
        return {}

    logger.info(
        "store_messages_node_triggered",
        session_id=session_id,
        message_count=len(new_messages),
        last_message_index=last_message_index,
    )

    # Store ALL new messages to mem0 (including tool messages for context)
    openai_msgs = cast(list[dict], convert_to_openai_messages(new_messages))
    asyncio.create_task(memory_service.add(user_id, openai_msgs, metadata))

    # Store only user/assistant messages to SQL (for conversation history)
    sql_messages = []
    for msg in new_messages:
        if isinstance(msg, HumanMessage) and msg.content:
            sql_messages.append({"role": "user", "content": str(msg.content)})
        elif isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
            sql_messages.append({"role": "assistant", "content": str(msg.content)})

    if sql_messages:
        asyncio.create_task(
            message_service.store_messages(
                user_id=int(user_id),
                session_id=session_id,
                messages=sql_messages,
            )
        )

    return {}
