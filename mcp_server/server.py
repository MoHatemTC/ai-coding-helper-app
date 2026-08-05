"""Standalone MCP server exposing agent tools via the Model Context Protocol.

Fully independent of ``app.*``: running this module never imports or executes
any application code. Guardrails and tool wiring live in
``mcp_server.tool_registry``.

Run with::

    uv run python -m mcp_server.server

Or register as a stdio MCP server in your Cline/VSCode config.
"""

from mcp.server.fastmcp import FastMCP

from mcp_server.services.vector_store import warmup_embedder
from mcp_server.tool_registry import register_all_tools
from mcp_server.tools import TOOLS

mcp = FastMCP(
    name="ai-coding-helper-tools",
    instructions="""MCP server for the AI Coding Helper agent.

Provides tools for:
- MCP server status and connectivity checks
- Web search via Tavily
- Code search over uploaded code chunks (scoped to a session/user)
- Long-term memory search
- Human-in-the-loop confirmation
""",
)

register_all_tools(mcp, TOOLS)


if __name__ == "__main__":
    # Load the embedding model up front under one-time stdout-fd suppression
    # (the only known C/Rust stdout writer) before the stdio JSON-RPC loop
    # starts, so the protocol channel can never be corrupted by model loading.
    warmup_embedder()
    mcp.run(transport="stdio")
