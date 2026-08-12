"""Long-term memory search tool for the standalone MCP server."""

from langchain_core.tools import tool

from mcp_server.identity import resolve_user_id
from mcp_server.services.memory import memory_service


@tool
# user_id is auto-injected scope, intentionally not documented.
async def memory_search(  # noqa: D417
    query: str, user_id: str
) -> str:
    """Search the long-term memory store for relevant past context about a user or topic.

    Args:
        query: The search query describing what to look for.

    Returns:
        Relevant memory entries as a string.

    Memories are automatically scoped to the current user.
    """
    resolved_user_id = resolve_user_id(user_id)
    result = await memory_service.search(user_id=resolved_user_id, query=query)
    return result if result else "No relevant memories found."
