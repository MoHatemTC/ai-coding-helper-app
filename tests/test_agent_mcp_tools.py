"""Tests that MCP tools are actually usable through the agent, not just
reachable via the raw MCP protocol (see test_mcp_integration.py for that).

Run with::

    uv run pytest tests/test_agent_mcp_tools.py -v
"""

import asyncio

from app.core.langgraph.tools.mcp_tools import get_mcp_tools


def test_get_mcp_tools_returns_bindable_tools():
    """The exact function graph.py's create_graph() calls. If this returns
    an empty list, the agent silently has no web_search/ask_human/
    memory_search -- get_mcp_tools() fails closed on connection errors, so
    an empty list here means something is actually wrong, not that
    everything's fine."""
    tools = asyncio.run(get_mcp_tools())
    names = {t.name for t in tools}
    assert names == {"web_search", "ask_human", "memory_search"}, names


def test_mcp_web_search_tool_invokes_through_langchain_interface():
    """Confirms the MCP-derived tool object behaves like any other
    LangChain BaseTool -- the same .ainvoke() interface graph.py's
    _execute_tool() calls -- not just that the raw MCP session works."""

    async def _run():
        tools = await get_mcp_tools()
        web_search = next(t for t in tools if t.name == "web_search")
        return await web_search.ainvoke({"query": "FastAPI latest release"})

    result = asyncio.run(_run())
    assert result, "expected non-empty result from web_search via LangChain tool interface"


def test_full_agent_turn_uses_an_mcp_tool(client):
    """End-to-end through the real FastAPI endpoint: send a prompt that
    should make the agent call web_search, and confirm a tool call to the
    MCP-backed tool actually shows up in the response/trace.

    `client` here is assumed to be your existing FastAPI TestClient fixture
    (conftest.py) used elsewhere in your test suite -- swap in whatever
    fixture name your project already uses for the chat endpoint.
    """
    response = client.post(
        "/api/v1/chatbot",
        json={
            "thread_id": "test-mcp-agent-turn",
            "message": "Search the web for the latest FastAPI release and tell me the version.",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # Adjust this assertion to match your actual response schema -- e.g. if
    # tool calls are exposed in a `messages` or `trace` field, check that a
    # message with name == "web_search" appears there instead of just
    # eyeballing the final text.
    assert body