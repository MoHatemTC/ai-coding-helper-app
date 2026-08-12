"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes tools for web search
and other external integrations.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .code_search import search_code, set_session_id, set_user_id
from .duckduckgo_search import duckduckgo_search_tool

tools: list[BaseTool] = [duckduckgo_search_tool, ask_human, search_code]

__all__ = ["tools", "set_session_id", "set_user_id"]
