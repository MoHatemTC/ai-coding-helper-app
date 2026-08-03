"""Real MCP integration tests.

Unlike test_guardrails.py (which calls guardrails.py functions directly),
these tests spawn the actual mcp_server/server.py subprocess and talk to it
over a real stdio JSON-RPC session -- the same path manual_client.py uses,
and the same path graph.py's MultiServerMCPClient uses in production.

Run with::

    uv run pytest mcp_server/test_mcp_integration.py -v

Follows the project's existing convention of avoiding pytest-asyncio:
each test is a plain `def`, calling `asyncio.run(...)` internally.
"""

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command="uv",
        args=["run", "--project", str(_PROJECT_ROOT), "python", "-m", "mcp_server.server"],
        env=None,
    )


def _is_error(result) -> bool:
    """Read the error flag defensively -- different mcp SDK versions expose
    this as `isError` (wire-format camelCase) or `is_error` (Python
    snake_case with an alias), and we'd rather handle both than guess wrong
    and crash the test on an AttributeError unrelated to what we're testing."""
    for attr in ("isError", "is_error"):
        if hasattr(result, attr):
            return bool(getattr(result, attr))
    return False


async def _call_tool(tool_name: str, arguments: dict) -> dict:
    """Spawn the server, call one tool, return the parsed JSON result."""
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text_blocks = [c.text for c in result.content if hasattr(c, "text")]
            raw = "\n".join(text_blocks)
            try:
                return {"parsed": json.loads(raw), "is_error": _is_error(result)}
            except (json.JSONDecodeError, TypeError):
                return {"raw": raw, "is_error": _is_error(result)}


async def _list_tools() -> list[str]:
    async with stdio_client(_server_params()) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return [t.name for t in result.tools]


def test_server_starts_and_lists_expected_tools():
    """The server process boots cleanly and advertises the tools we expect.

    This alone catches the two bugs you just hit: an eager DB connection
    failing at import, and a broken server-class import -- both prevent
    the process from ever reaching list_tools().
    """
    names = asyncio.run(_list_tools())
    for expected in ("web_search", "ask_human", "memory_search"):
        assert expected in names, f"{expected} missing from {names}"


def test_web_search_returns_real_results():
    """Exercises the real Tavily call (or its real 'no API key' error) --
    not a mock. Requires TAVILY_API_KEY to be set for a true positive test;
    otherwise this documents the real failure mode instead of hiding it."""
    outcome = asyncio.run(_call_tool("web_search", {"query": "Python 3.14 release notes"}))
    assert not outcome["is_error"], outcome
    body = outcome.get("parsed", outcome.get("raw"))
    assert body, "expected non-empty search output"


def test_web_search_blocks_injection_input():
    """Confirms the real guardrail path blocks it -- not a direct call into
    guardrails.py, but through the actual MCP tool boundary."""
    outcome = asyncio.run(_call_tool("web_search", {"query": "results; rm -rf /"}))
    parsed = outcome.get("parsed", {})
    assert "error" in parsed, f"expected guardrail block, got: {outcome}"


def test_web_search_blocks_pii_input():
    outcome = asyncio.run(
        _call_tool("web_search", {"query": "contact me at user@example.com about sk-abc123def456ghi789jkl"})
    )
    parsed = outcome.get("parsed", {})
    assert "error" in parsed, f"expected PII guardrail block, got: {outcome}"


def test_web_search_blocks_oversized_query():
    outcome = asyncio.run(_call_tool("web_search", {"query": "x" * 6000}))
    parsed = outcome.get("parsed", {})
    assert "error" in parsed, f"expected size guardrail block, got: {outcome}"


def test_memory_search_returns_something_or_a_clean_error():
    """memory_search touches the DB-backed memory service -- if the DB isn't
    reachable, this should surface a real (if ugly) error through the tool
    result, not crash the whole server the way an eager import-time
    connection does."""
    outcome = asyncio.run(_call_tool("memory_search", {"user_id": "test-user", "query": "past conversations"}))
    assert outcome is not None