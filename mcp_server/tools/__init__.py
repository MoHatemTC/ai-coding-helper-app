"""Tool set for the standalone MCP server.

Guardrails are NOT applied here — they are applied centrally by
``mcp_server.tool_registry`` when each tool is registered. Tool functions stay
responsible only for their own logic and per-tool authorization checks.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .memory_search import memory_search
from .search_code import search_code
from .server_status import server_status
from .web_search import web_search

TOOLS: list[BaseTool] = [server_status, web_search, search_code, memory_search, ask_human]

__all__ = ["TOOLS"]
