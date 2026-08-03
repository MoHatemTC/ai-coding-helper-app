"""MCP Server exposing agent tools via the Model Context Protocol.

Run with::

    uv run python -m mcp_server.server

Or register as a stdio MCP server in your Cline/VSCode config.
"""

import sys

# CRITICAL: Redirect stdout to stderr BEFORE any imports.
# The MCP stdio protocol expects stdout to contain ONLY JSON-RPC messages.
# The app's structlog logging and FastMCP's debug prints go to stdout by
# default, which corrupts the protocol. Redirecting stdout to stderr ensures
# all logging output goes to stderr (which Cline/the client ignores) while
# keeping stdout clean for JSON-RPC only.
_real_stdout = sys.stdout
sys.stdout = sys.stderr

import json  # noqa: E402  (import order is intentional — see stdout redirect above)
import traceback  # noqa: E402  (import order is intentional — see stdout redirect above)

from mcp.server import MCPServer  # noqa: E402  (import order is intentional)

from app.core.langgraph.tools.tavily_search import tavily_search_tool  # noqa: E402
from app.core.langgraph.tools.ask_human import ask_human as ask_human_tool  # noqa: E402
from app.services.memory import memory_service  # noqa: E402


from mcp_server.guardrails import (  # noqa: E402  (import order is intentional)
    GuardrailError,
    apply_input_guardrails,
    apply_output_guardrails,
)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = MCPServer(
    name="ai-coding-helper-tools",
    instructions="""MCP server for the AI Coding Helper agent.

Provides tools for:
- Web search via Tavily
- Human-in-the-loop confirmation
- Long-term memory search and storage
""",
)


# ---------------------------------------------------------------------------
# Tool: web_search  # noqa: ERA001
# ---------------------------------------------------------------------------


@mcp.tool(
    name="web_search",
    description="Search the web using Tavily. Returns up to 10 results.",
)
async def web_search(query: str) -> str:
    """Search the web using Tavily.

    Args:
        query: The search query.

    Returns:
        Search results as a string.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits)
        apply_input_guardrails(
            "web_search",
            {"query": query},
            check_pii_flag=True,  # block PII in search queries
        )

        # Check if Tavily API key is configured
        if tavily_search_tool is None:
            return json.dumps(
                {
                    "error": "TAVILY_API_KEY is not configured. Set it in your .env file to enable web search.",
                }
            )

        # Tavily search is synchronous; run it
        result = tavily_search_tool.invoke(query)
        if not isinstance(result, str):
            result = str(result)

        # Apply output guardrails (PII redaction + truncation)
        return apply_output_guardrails(result, tool_name="web_search")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"Search failed: {e!s}"})


# ---------------------------------------------------------------------------
# Tool: ask_human  # noqa: ERA001
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ask_human",
    description="Pause execution and ask the user a question. "
    "Use this when you need clarification or confirmation before proceeding.",
)
async def ask_human(question: str) -> str:
    """Ask the user a question and wait for their response.

    Args:
        question: The question to ask the user.

    Returns:
        The user's response.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits)
        apply_input_guardrails(
            "ask_human",
            {"question": question},
            check_pii_flag=True,  # block PII in questions to protect user privacy
        )

        # This will interrupt graph execution via langgraph's `interrupt`
        response = ask_human_tool.invoke({"question": question})

        # Apply output guardrails
        return apply_output_guardrails(str(response), tool_name="ask_human")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"ask_human failed: {e!s}"})


# ---------------------------------------------------------------------------
# Tool: memory_search  # noqa: ERA001
# ---------------------------------------------------------------------------


@mcp.tool(
    name="memory_search",
    description="Search the long-term memory store for relevant past context about a user or topic.",
)
async def memory_search(user_id: str, query: str) -> str:
    """Search long-term memory for relevant past context.

    Args:
        user_id: The user identifier to search memories for.
        query: The search query describing what to look for.

    Returns:
        Relevant memory entries as a string.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits, PII scan)
        apply_input_guardrails(
            "memory_search",
            {"user_id": user_id, "query": query},
            check_pii_flag=True,  # block PII in memory searches
        )

        result = await memory_service.search(user_id=user_id, query=query)
        output = result if result else "No relevant memories found."

        # Apply output guardrails (PII redaction + truncation)
        return apply_output_guardrails(output, tool_name="memory_search")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"Memory search failed: {e!s}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Restore stdout before starting the MCP server — the MCP stdio transport
    # uses stdout to send JSON-RPC responses to the client. During imports,
    # stdout was redirected to stderr to keep logging output out of the
    # JSON-RPC stream. Now that imports are done, restore stdout so the
    # MCP server can communicate with the client.
    sys.stdout = _real_stdout
    print("Starting AI Coding Helper MCP server on stdio...", file=sys.stderr)
    mcp.run(transport="stdio")
