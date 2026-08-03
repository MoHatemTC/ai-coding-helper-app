"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes tools for web search
and other external integrations.
"""

from langchain_core.tools.base import BaseTool



# web_search, ask_human, and memory_search now come from the MCP server
# (mcp_server/server.py) at graph-creation time -- see
# app/core/langgraph/tools/mcp_tools.py -- so they're deliberately not bound
# here anymore. Binding both the local versions and the MCP versions would
# give the agent two differently-named tools doing the same job.
agent_tools: list[BaseTool] = []

# correctness / security / performance reviews used to be forced graph nodes
# that ran on every turn. They're tools now: the agent decides whether and
# when to call them, and can call more than one in the same step to run them
# concurrently. These stay local/native since nothing outside this agent
# needs to call them.


# NOTE: `tools` here is the fully *local* set only. MCP tools are fetched
# asynchronously (they require spawning/talking to a subprocess), so they
# can't be included in this module-level list -- graph.py's create_graph()
# merges get_mcp_tools() in at graph-build time instead. Don't treat `tools`
# as "everything the agent can call"; it's "everything native the agent can
# call" until that merge happens.
tools: list[BaseTool] = [*agent_tools]

__all__ = [
    "agent_tools",
    "tools",
]