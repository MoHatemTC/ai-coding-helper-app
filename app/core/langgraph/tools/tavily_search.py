"""Tavily search tool for LangGraph.

This module provides a Tavily search tool that can be used with LangGraph
to perform web searches. It returns up to 10 search results and handles errors
gracefully.

Requires a TAVILY_API_KEY environment variable.
"""

import os
import logging

from langchain_tavily import TavilySearch

logger = logging.getLogger(__name__)

_api_key = os.getenv("TAVILY_API_KEY")
if _api_key:
    tavily_search_tool = TavilySearch(max_results=10, tavily_api_key=_api_key, handle_tool_error=True)
else:
    tavily_search_tool = None
    logger.warning(
        "TAVILY_API_KEY environment variable not set. "
        "The web_search tool will return an error when called. "
        "Set TAVILY_API_KEY in your .env file or environment."
    )