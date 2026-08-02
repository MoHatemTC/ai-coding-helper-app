"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes tools for web search
and other external integrations.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .review_code import review_code
from .tavily_search import tavily_search_tool

agent_tools: list[BaseTool] = [t for t in [tavily_search_tool, ask_human] if t is not None]
review_tools: list[BaseTool] = [review_code]
tools: list[BaseTool] = [*agent_tools, *review_tools]
