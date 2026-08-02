"""Unified code-review capability for the agent."""

import asyncio
import json
from typing import Any

from langchain_core.tools import tool

from app.core.langgraph.nodes.correctness import correctness_node
from app.core.langgraph.nodes.performance_node import performance_review_node
from app.core.langgraph.nodes.security_review import security_review_node

_CATEGORY_PRIORITY = {
    "correctness": 0,
    "security": 1,
    "performance": 2,
    "style": 3,
}
_SEVERITY_PRIORITY = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _enum_value(value: Any) -> str:
    """Return a stable string for enum or plain-string finding fields."""
    return str(getattr(value, "value", value)).lower()


def _prioritize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order findings by user impact so blockers are presented first."""
    return sorted(
        findings,
        key=lambda finding: (
            _CATEGORY_PRIORITY.get(_enum_value(finding.get("category")), 99),
            _SEVERITY_PRIORITY.get(_enum_value(finding.get("severity")), 99),
            finding.get("line", 0),
        ),
    )


@tool
async def review_code(code: str, language: str = "") -> str:
    """Run exhaustive correctness, security, and performance reviews in parallel for submitted code."""
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

    findings = _prioritize_findings(
        [
            *correctness_result.get("findings", []),
            *security_result.get("findings", []),
            *performance_result.get("findings", []),
        ]
    )
    syntax_blockers = [
        finding
        for finding in findings
        if _enum_value(finding.get("category")) == "correctness"
        and "syntax" in str(finding.get("message", "")).lower()
    ]

    return json.dumps(
        {
            "reviewers": [
                "correctness",
                "security",
                "performance",
            ],
            "finding_count": len(findings),
            "syntax_blockers": syntax_blockers,
            "findings": findings,
        }
    )
