"""Dynamic tool registration for the standalone MCP server.

LangChain ``BaseTool`` objects are registered onto a FastMCP server with:

- per-tool ``GuardrailPolicy`` (sidecar config keyed by ``tool.name``)
- a dynamically built async wrapper whose ``__signature__``/``__annotations__``
  are derived from the tool's input schema (FastMCP's ``func_metadata`` reads
  ``inspect.signature``, so the exposed JSON schema preserves types and
  optional defaults)
- error handling: guardrail violations and unexpected exceptions are returned
  to the client as error JSON instead of crashing the call
- sync tools run in a thread via ``asyncio.to_thread`` (blocking work never
  stalls the event loop); stdout-fd suppression is NOT applied per call —
  the only known C/Rust stdout writer (the embedding model load) is suppressed
  once at startup by ``mcp_server.services.vector_store.warmup_embedder``
"""

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, cast

from langchain_core.tools import BaseTool
from mcp.server.fastmcp import FastMCP

from mcp_server.config import config as mcp_config
from mcp_server.guardrails import (
    FieldRule,
    GuardrailError,
    GuardrailPolicy,
    apply_input_guardrails,
    apply_output_guardrails,
)

# ---------------------------------------------------------------------------
# Per-tool guardrail policies (sidecar config, keyed by tool.name)
# ---------------------------------------------------------------------------

GUARDRAIL_CONFIG: dict[str, GuardrailPolicy] = {
    "server_status": GuardrailPolicy(check_pii=False),
    "web_search": GuardrailPolicy(field_rules={"query": FieldRule.QUERY}, check_pii=True),
    "search_code": GuardrailPolicy(
        field_rules={
            "query": FieldRule.QUERY,
            "user_id": FieldRule.USER_ID,
            "session_id": FieldRule.GENERAL,
            "file_name": FieldRule.GENERAL,
        },
        check_pii=True,
    ),
    "memory_search": GuardrailPolicy(
        field_rules={"query": FieldRule.QUERY, "user_id": FieldRule.USER_ID},
        check_pii=True,
    ),
}


@dataclass(frozen=True)
class ToolConfig:
    """Registration metadata for a tool."""

    guardrail_policy: GuardrailPolicy | None = None
    guardrails_enabled: bool = True


TOOL_CONFIGS: dict[str, ToolConfig] = {
    name: ToolConfig(guardrail_policy=policy) for name, policy in GUARDRAIL_CONFIG.items()
}


def register_all_tools(mcp: FastMCP, tools: list[BaseTool]) -> None:
    """Register every LangChain tool onto the FastMCP server with guardrails."""
    enabled = mcp_config.guardrails_enabled
    for tool in tools:
        config = TOOL_CONFIGS.get(tool.name, ToolConfig())
        _register_tool(mcp, tool, config, guardrails_enabled=enabled)


# ---------------------------------------------------------------------------
# Signature derivation
# ---------------------------------------------------------------------------


def _type_from_schema(schema: dict[str, Any]) -> Any:
    """Map a JSON-schema type to a Python type annotation."""
    type_ = schema.get("type")
    if isinstance(type_, list):
        non_null = [item for item in type_ if item != "null"]
        type_ = non_null[0] if non_null else None
    if type_ == "integer":
        return int
    if type_ == "number":
        return float
    if type_ == "boolean":
        return bool
    if type_ == "array":
        return list
    if type_ == "object":
        return dict
    return str


def _build_signature(input_schema: dict[str, Any]) -> inspect.Signature:
    """Build an inspect.Signature that mirrors the tool's input schema."""
    parameters: list[inspect.Parameter] = []
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []) or [])
    for name in properties:
        annotation = _type_from_schema(properties[name])
        if name in required:
            parameters.append(inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation))
        else:
            parameters.append(
                inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None, annotation=annotation)
            )
    return inspect.Signature(parameters=parameters)


# ---------------------------------------------------------------------------
# Wrapper & execution
# ---------------------------------------------------------------------------


def _run_sync(tool: BaseTool, kwargs: dict[str, Any]) -> Any:
    """Invoke a sync tool off the event loop."""
    return tool.invoke(kwargs)


async def _execute(
    tool: BaseTool,
    policy: GuardrailPolicy | None,
    kwargs: dict[str, Any],
    *,
    guardrails_enabled: bool = True,
) -> str:
    """Apply guardrails (when enabled), run the tool, and sanitize its output."""
    if guardrails_enabled:
        try:
            apply_input_guardrails(tool.name, kwargs, policy=policy)
        except GuardrailError as e:
            return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})

    try:
        if getattr(tool, "coroutine", None) is not None:
            result = await tool.ainvoke(kwargs)
        else:
            result = await asyncio.to_thread(_run_sync, tool, kwargs)
        if not isinstance(result, str):
            result = str(result)
        if guardrails_enabled:
            return apply_output_guardrails(result, tool_name=tool.name)
        return result
    except GuardrailError as e:
        return json.dumps({"error": str(e), "reason": e.reason, "field": e.field})
    except Exception as e:
        return json.dumps({"error": f"{tool.name} failed: {e!s}"})


def _register_tool(mcp: FastMCP, tool: BaseTool, config: ToolConfig, guardrails_enabled: bool) -> None:
    """Register a single tool with a dynamically-derived schema."""
    schema = cast(Any, tool.get_input_schema())
    schema = schema.model_json_schema() if isinstance(schema, type) else schema
    signature = _build_signature(schema)

    async def _run(**kwargs: Any) -> str:
        """Guardrail-wrapped, dynamically-signed tool handler."""
        return await _execute(
            tool,
            config.guardrail_policy,
            kwargs,
            guardrails_enabled=config.guardrails_enabled and guardrails_enabled,
        )

    _run.__name__ = f"{tool.name}_wrapper"
    _run.__qualname__ = f"tools.{tool.name}"
    setattr(_run, "__signature__", signature)  # noqa: B010  (pyright rejects attribute assignment)
    _run.__annotations__ = {param.name: param.annotation for param in signature.parameters.values()}

    mcp.add_tool(_run, name=tool.name, description=tool.description)
