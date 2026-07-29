"""MCP Server exposing agent tools via the Model Context Protocol.

Run with::

    uv run python -m mcp_server.server

Or register as a stdio MCP server in your Cline/VSCode config.
"""

import json
import sys
import traceback
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.core.langgraph.tools.duckduckgo_search import duckduckgo_search_tool
from app.core.langgraph.tools.ask_human import ask_human as ask_human_tool
from app.tools.review_tool import review_correctness
from app.services.memory import memory_service
from app.schemas.review import Finding as ReviewFinding

from mcp_server.guardrails import (
    GuardrailError,
    apply_input_guardrails,
    apply_output_guardrails,
    check_metadata_safety,
    redact_sensitive_params,
    get_audit_log,
)

# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="ai-coding-helper-tools",
    instructions="""MCP server for the AI Coding Helper agent.

Provides tools for:
- Code review (correctness checking)
- Web search via DuckDuckGo
- Human-in-the-loop confirmation
- Long-term memory search and storage
""",
)

# ---------------------------------------------------------------------------
# Tool: review_code
# ---------------------------------------------------------------------------


@mcp.tool(
    name="review_code",
    description="Run a correctness review on submitted source code. "
    "Accepts code and a language identifier, returns a list of findings.",
)
async def review_code(code: str, language: str = "") -> str:
    """Run a correctness review on submitted source code.

    Args:
        code: The source code to review.
        language: The programming language (e.g. 'python', 'javascript').

    Returns:
        JSON string with review findings.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits, PII scan)
        apply_input_guardrails(
            "review_code",
            {"code": code, "language": language},
            check_pii_flag=False,  # code legitimately contains credentials sometimes
        )

        # Run the existing rule-based review tool
        raw_findings: list[ReviewFinding] = review_correctness(code)

        findings_list = []
        for f in raw_findings:
            findings_list.append({
                "line": f.line,
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "category": f.category.value if hasattr(f.category, "value") else str(f.category),
                "message": f.message,
                "rationale": f.rationale,
            })

        result = json.dumps({"findings": findings_list, "finding_count": len(findings_list)}, indent=2)

        # Apply output guardrails (PII redaction + truncation)
        return apply_output_guardrails(result, tool_name="review_code")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field, "findings": []})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"Review failed: {e!s}", "findings": []})


# ---------------------------------------------------------------------------
# Tool: web_search
# ---------------------------------------------------------------------------


@mcp.tool(
    name="web_search",
    description="Search the web using DuckDuckGo. Returns up to 10 results.",
)
async def web_search(query: str) -> str:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query.

    Returns:
        Search results as a string.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits)
        apply_input_guardrails(
            "web_search",
            {"query": query},
            check_pii_flag=True,  # block PII in search queries
        )

        # DuckDuckGo search is synchronous; run it
        result = duckduckgo_search_tool.invoke(query)
        if not isinstance(result, str):
            result = str(result)

        # Apply output guardrails (PII redaction + truncation)
        return apply_output_guardrails(result, tool_name="web_search")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"Search failed: {e!s}"})


# ---------------------------------------------------------------------------
# Tool: ask_human
# ---------------------------------------------------------------------------


@mcp.tool(
    name="ask_human",
    description="Pause execution and ask the user a question. "
    "Use this when you need clarification or confirmation before proceeding.",
)
async def ask_human(question: str) -> str:
    """Ask the user a question and wait for their response.

    Args:
        question: The question to ask the user.

    Returns:
        The user's response.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits)
        apply_input_guardrails(
            "ask_human",
            {"question": question},
            check_pii_flag=True,  # block PII in questions to protect user privacy
        )

        # This will interrupt graph execution via langgraph's `interrupt`
        response = ask_human_tool.invoke({"question": question})

        # Apply output guardrails
        return apply_output_guardrails(str(response), tool_name="ask_human")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"ask_human failed: {e!s}"})


# ---------------------------------------------------------------------------
# Tool: memory_search
# ---------------------------------------------------------------------------


@mcp.tool(
    name="memory_search",
    description="Search the long-term memory store for relevant past context about a user or topic.",
)
async def memory_search(user_id: str, query: str) -> str:
    """Search long-term memory for relevant past context.

    Args:
        user_id: The user identifier to search memories for.
        query: The search query describing what to look for.

    Returns:
        Relevant memory entries as a string.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits, PII scan)
        apply_input_guardrails(
            "memory_search",
            {"user_id": user_id, "query": query},
            check_pii_flag=True,  # block PII in memory searches
        )

        result = await memory_service.search(user_id=user_id, query=query)
        output = result if result else "No relevant memories found."

        # Apply output guardrails (PII redaction + truncation)
        return apply_output_guardrails(output, tool_name="memory_search")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"Memory search failed: {e!s}"})


# ---------------------------------------------------------------------------
# Tool: memory_add
# ---------------------------------------------------------------------------


@mcp.tool(
    name="memory_add",
    description="Store a message or piece of information into long-term memory for a user.",
)
async def memory_add(user_id: str, content: str, metadata: str = "{}") -> str:
    """Store information into long-term memory.

    Args:
        user_id: The user identifier to store memory for.
        content: The content to remember.
        metadata: Optional JSON metadata string (e.g. '{"type": "note", "source": "chat"}').

    Returns:
        Confirmation message.
    """
    try:
        # Apply all input guardrails (rate limit, injection checks, size limits, PII scan)
        apply_input_guardrails(
            "memory_add",
            {"user_id": user_id, "content": content},
            check_pii_flag=True,  # alert on PII going into memory
        )

        # Validate metadata separately
        try:
            parsed_metadata = check_metadata_safety(metadata)
        except GuardrailError as e:
            return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})

        messages = [{"role": "user", "content": content}]
        await memory_service.add(user_id=user_id, messages=messages, metadata=parsed_metadata)

        output = json.dumps({"status": "ok", "message": "Memory stored successfully."})

        # Apply output guardrails
        return apply_output_guardrails(output, tool_name="memory_add")

    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        traceback.print_exc()
        return json.dumps({"error": f"Memory add failed: {e!s}"})


# ---------------------------------------------------------------------------
# Extra: audit log access
# ---------------------------------------------------------------------------


@mcp.tool(
    name="get_audit_log",
    description="[ADMIN] Retrieve the audit log of tool calls and guardrail events. "
    "Returns the last N entries.",
)
async def get_audit_log_tool(limit: int = 50) -> str:
    """Retrieve the audit log of tool calls and guardrail events.

    Args:
        limit: Maximum number of log entries to return (default 50, max 200).

    Returns:
        JSON string with audit log entries.
    """
    try:
        limit = min(max(1, limit), 200)
        log_entries = get_audit_log()[-limit:]
        return json.dumps({"entries": log_entries, "count": len(log_entries)}, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Failed to retrieve audit log: {e!s}"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting AI Coding Helper MCP server on stdio...", file=sys.stderr)
    mcp.run(transport="stdio")