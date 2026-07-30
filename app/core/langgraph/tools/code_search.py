"""Code search tool for retrieving relevant code chunks from pgvector."""

import contextvars

from langchain_core.tools import tool

from app.services.vector_store import vector_store_service

# Set before graph invocation so tools can scope to the current session/user
current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_session_id", default=None)
current_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar("current_user_id", default=None)


def set_session_id(session_id: str) -> None:
    """Set the session ID for the current invocation context."""
    current_session_id.set(session_id)


def set_user_id(user_id: int) -> None:
    """Set the user ID for the current invocation context."""
    current_user_id.set(user_id)


@tool
def search_code(query: str, file_name: str | None = None) -> str:
    """Search uploaded code files for relevant code chunks.

    Use this tool when you need to find relevant code from files the user
    has uploaded in this session. The search uses semantic similarity, so
    you can ask about functionality, patterns, or concepts — not just
    exact keywords.

    Args:
        query: A natural-language description of what code you're looking for.
               Be specific about the functionality or topic.
        file_name: Optional — narrow results to a specific filename
                   (e.g. 'skill_profile.py', 'config.py').

    Returns:
        Formatted string of matching code chunks with file info and content.
    """
    session_id = current_session_id.get()
    if not session_id:
        return "Error: no active session"

    user_id = current_user_id.get()
    if not user_id:
        return "Error: no authenticated user"

    results = vector_store_service.search(
        query=query,
        session_id=session_id,
        user_id=user_id,
        file_name=file_name,
    )

    if not results:
        return "No matching code found."

    lines = []
    for chunk in results:
        header = f"[{chunk.file_name} ({chunk.language})] created: {chunk.created_at.isoformat() if chunk.created_at else 'unknown'}"
        lines.append(f"{header}\n```\n{chunk.content}\n```")

    return "\n---\n".join(lines)
