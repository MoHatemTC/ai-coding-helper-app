"""Server status tool for the standalone MCP server."""

import json

from langchain_core.tools import tool


@tool
def server_status() -> str:
    """Return a dependency-free status payload for demonstrating MCP connectivity."""
    return json.dumps(
        {
            "status": "ok",
            "server": "ai-coding-helper-tools",
            "protocol": "MCP",
            "transport": "stdio",
            "message": "MCP server is ready to receive tool calls.",
        }
    )
