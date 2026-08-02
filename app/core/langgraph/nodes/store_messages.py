"""Store the user turn and approved final response without persisting a draft."""

from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig

from app.core.logging import logger
from app.services.memory import memory_service
from app.services.message import message_service
from app.services.skill_profile import skill_profile_service


async def store_messages_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Persist only the sanitized user message and final approved assistant response."""
    metadata = config.get("metadata", {})
    user_id = metadata.get("user_id")
    session_id = metadata.get("session_id")
    if not user_id or not session_id:
        return {}

    messages = state.get("messages", [])
    human_message = next(
        (message for message in reversed(messages) if isinstance(message, HumanMessage)), None
    )
    final_response = state.get("final_response", "")
    user_query_redacted = state.get("user_query_redacted", False)
    file_dicts = [attachment.model_dump() for attachment in state.get("uploaded_files", [])] or None

    sql_messages: list[dict[str, Any]] = []
    if human_message and human_message.content:
        user_entry: dict[str, Any] = {"role": "user", "content": str(human_message.content)}
        if file_dicts:
            user_entry["files"] = file_dicts
        sql_messages.append(user_entry)
    if isinstance(final_response, str) and final_response:
        sql_messages.append({"role": "assistant", "content": final_response})

    if sql_messages:
        await message_service.store_messages(
            user_id=int(user_id), session_id=session_id, messages=sql_messages
        )

    # Redacted human input remains available in the checkpoint but is never
    # promoted to semantic long-term memory. The approved assistant answer is.
    memory_messages: list[dict[str, str]] = []
    if human_message and human_message.content and not user_query_redacted:
        memory_messages.append({"role": "user", "content": str(human_message.content)})
    if isinstance(final_response, str) and final_response:
        memory_messages.append({"role": "assistant", "content": final_response})
    if memory_messages:
        await memory_service.add(str(user_id), memory_messages, metadata)

    conversation_text = "\n".join(
        f"{message['role']}: {message['content']}" for message in memory_messages
    )
    if conversation_text:
        skill_profile_service.schedule_update(int(user_id), conversation_text)

    logger.info(
        "approved_messages_stored",
        session_id=session_id,
        user_id=user_id,
        user_query_redacted=user_query_redacted,
        assistant_response_stored=bool(final_response),
    )
    return {}
