"""Unified code-review capability for the agent."""

import asyncio
import json
from typing import Any

from langchain_core.tools import tool

from app.core.langgraph.nodes.correctness import correctness_node
from app.core.langgraph.nodes.performance_node import performance_review_node
from app.core.langgraph.nodes.security_review import security_review_node


@tool
async def review_code(code: str, language: str = "") -> str:
    """Run correctness, security, and performance reviews in parallel."""
    state: dict[str, Any] = {
        "code": code,
        "sanitized_code": code,
        "language": language or None,
    }

    correctness_result, security_result, performance_result = await asyncio.gather(
        asyncio.to_thread(correctness_node, state),
        security_review_node(state),
        performance_review_node(state),
    )

    findings = [
        *correctness_result.get("findings", []),
        *security_result.get("findings", []),
        *performance_result.get("findings", []),
    ]

    return json.dumps(
        {
            "reviewers": [
                "correctness",
                "security",
                "performance",
            ],
            "finding_count": len(findings),
            "findings": findings,
        }
    )