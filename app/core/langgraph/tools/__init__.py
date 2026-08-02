"""LangGraph tools for enhanced language model capabilities.

This package contains custom tools that can be used with LangGraph to extend
the capabilities of language models. Currently includes tools for web search
and other external integrations.
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
<<<<<<< HEAD
from .duckduckgo_search import duckduckgo_search_tool
from .review_code import review_code

agent_tools: list[BaseTool] = [
    duckduckgo_search_tool,
    ask_human,
]

review_tools: list[BaseTool] = [review_code]
tools: list[BaseTool] = [*agent_tools, *review_tools]
=======
from .tavily_search import tavily_search_tool

tools: list[BaseTool] = [tavily_search_tool, ask_human]
>>>>>>> c86abce8d27c327cca3b396b4fbc9c4fcc0bb744
