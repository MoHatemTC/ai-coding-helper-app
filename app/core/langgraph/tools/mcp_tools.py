"""MCP-backed tools for the agent.

Spawns ``mcp_server/server.py`` as a stdio subprocess via
``MultiServerMCPClient`` and converts its guardrailed tools (web_search,
ask_human, memory_search) into ordinary LangChain BaseTool objects, so they
bind and get called exactly like ``review_code`` or the review tools.

These three are the single source of truth for web_search/ask_human/memory
search now -- the local duckduckgo/ask_human tools were removed from
``agent_tools`` in ``tools/__init__.py`` to avoid binding two versions of
the same tool.

IMPORTANT -- ask_human specifically: the MCP tool wraps
``app.core.langgraph.tools.ask_human``, which calls LangGraph's
``interrupt()``. ``interrupt()`` only pauses/resumes correctly when it runs
inside the same graph invocation it's interrupting, with that graph's own
checkpointer attached. Once ask_human runs inside the MCP server's *separate
subprocess*, interrupt() no longer has access to the main graph's execution
context -- it will not pause your graph.py graph the way it did when
ask_human was called in-process. Test this specific path (call ask_human via
MCP mid-conversation and confirm the graph actually pauses for input) before
relying on it; if it doesn't work, ask_human is a case where the tool should
stay native rather than go through MCP, even though web_search and
memory_search are fine.
"""

import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

# Derived automatically (app/core/langgraph/tools/mcp_tools.py -> project
# root is four levels up) instead of hardcoded, so this can't silently point
# at a machine it wasn't written on. Adjust the parents[N] index if this
# file's depth in the tree changes.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_mcp_client = MultiServerMCPClient(
    {
        "ai_coding_helper_tools": {
            "command": "uv",
            "args": [
                "run",
                "--project",
                str(_PROJECT_ROOT),
                "python",
                "-m",
                "mcp_server.server",
            ],
            "transport": "stdio",
        }
    }
)


async def get_mcp_tools() -> list:
    """Fetch and convert MCP tools from the running server subprocess.

    Call once during graph creation (it's already async) and merge the
    result into ``bound_tools`` before compiling the graph -- don't call
    this per-request, since it spawns/talks to a subprocess each time
    unless you cache the result.
    """
    try:
        return await _mcp_client.get_tools()
    except Exception:
        # Fail closed on the *connection*, not just tool calls: if the MCP
        # server can't be reached, the agent should run with its remaining
        # local tools rather than crash graph creation entirely.
        print("Warning: could not connect to MCP server; continuing without its tools", file=sys.stderr)
        return []

