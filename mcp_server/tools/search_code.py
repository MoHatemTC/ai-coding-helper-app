"""Code search tool for the standalone MCP server (pgvector)."""

import json

from langchain_core.tools import tool

from mcp_server.guardrails import GuardrailError
from mcp_server.identity import resolve_user_id
from mcp_server.services.vector_store import (
    search_code_chunks,
    session_belongs_to_user,
)


@tool
# user_id/session_id are auto-injected scope, intentionally not documented.
def search_code(  # noqa: D417
    query: str, user_id: int, session_id: str, file_name: str | None = None
) -> str:
    """Search uploaded code files for relevant code chunks scoped to a session and user.

    Use this tool when you need to find relevant code from files uploaded in a
    session. The search uses semantic similarity, so you can ask about
    functionality, patterns, or concepts — not just exact keywords.

    Args:
        query: A natural-language description of what code you're looking for.
        file_name: Optional — narrow results to a specific filename.

    Returns:
        Formatted string of matching code chunks with file info and content.

    Results are automatically scoped to the current user and session.
    """
    try:
        resolved_user_id = int(resolve_user_id(str(user_id)))
    except ValueError as e:
        return json.dumps({"error": f"invalid user_id: {e}", "reason": "invalid_user_id", "field": "user_id"})

    if not session_id or not query.strip():
        return json.dumps(
            {"error": "query and session_id are required", "reason": "invalid_arguments", "field": "input"}
        )

    # Layer 1 backstop: reject a session/user pair that does not belong together.
    if not session_belongs_to_user(session_id, resolved_user_id):
        return json.dumps(
            {"error": "session not found for this user", "reason": "ownership_violation", "field": "session_id"}
        )

    try:
        results = search_code_chunks(
            query=query,
            session_id=session_id,
            user_id=resolved_user_id,
            file_name=file_name,
        )
    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        return json.dumps({"error": f"code search failed: {e!s}"})

    if not results:
        return "No matching code found."

    lines = []
    for chunk in results:
        created = chunk.created_at.isoformat() if chunk.created_at else "unknown"
        header = f"[{chunk.file_name} ({chunk.language})] created: {created}"
        lines.append(f"{header}\n```\n{chunk.content}\n```")

    return "\n---\n".join(lines)
