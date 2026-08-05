"""Web search tool for the standalone MCP server (Tavily)."""

import json

from langchain_core.tools import tool
from langchain_tavily import TavilySearch

from mcp_server.config import config

_tavily: TavilySearch | None = None


def _get_tavily() -> TavilySearch | None:
    """Lazily build the Tavily search client (None if no API key configured)."""
    global _tavily
    if _tavily is None:
        if config.tavily_api_key:
            _tavily = TavilySearch(
                max_results=10,
                tavily_api_key=config.tavily_api_key,
                handle_tool_error=True,
            )
    return _tavily


@tool
def web_search(query: str) -> str:
    """Search the web using Tavily. Returns up to 10 results.

    Args:
        query: The search query.

    Returns:
        Search results as a string.
    """
    tavily = _get_tavily()
    if tavily is None:
        return json.dumps(
            {
                "error": "TAVILY_API_KEY is not configured. Set it in your .env file to enable web search.",
            }
        )
    result = tavily.invoke(query)
    if not isinstance(result, str):
        result = str(result)
    return result
