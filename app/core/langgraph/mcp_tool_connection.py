"""Long-lived MCP tool connection shared across all agent users.

The standalone MCP server is spawned once as a stdio subprocess at app
startup and kept alive for the process lifetime; every user's tool calls
multiplex over the single JSON-RPC session. This avoids the default
``MultiServerMCPClient.get_tools()`` behaviour of spawning a throwaway
subprocess per tool call when no session is supplied.

Layer-3 scope security is unchanged: ``_ScopeForcingInterceptor`` overwrites
``user_id``/``session_id`` on user-scoped tools from the backend contextvars
before the call leaves the process, and the subprocess is spawned with
``MCP_USER_ID`` forced empty so it runs unbound. The agent-facing schema and
descriptions are scrubbed of those scope args (see ``_scrub_agent_visible_tool``)
so the model never sees or chooses them. If the session dies,
``MCPToolConnection.reset`` restarts the subprocess and each tool's retry
wrapper reconnects once before giving up.
"""

import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from app.core.config import settings
from app.core.langgraph.tools.code_search import (
    current_session_id,
    current_user_id,
)
from app.core.logging import logger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _ScopeForcingInterceptor:
    """Force user/session scope on user-scoped MCP tools from backend contextvars.

    The model never chooses the user_id/session_id: these args are overwritten
    with the values bound for the current request before the call leaves the
    process, mirroring the local ``search_code`` tool's security model. The
    tool name here is the raw MCP tool name (the adapters prefix the LangChain
    tool name, but pass the unprefixed name in the request).
    """

    _SCOPE_FIELDS: dict[str, tuple[str, ...]] = {
        "search_code": ("user_id", "session_id"),
        "memory_search": ("user_id",),
    }

    async def __call__(
        self,
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
    ) -> Any:
        fields = self._SCOPE_FIELDS.get(request.name)
        if fields is not None:
            args = dict(request.args)
            for field in fields:
                if field == "user_id":
                    args["user_id"] = str(current_user_id.get() or "")
                elif field == "session_id":
                    args["session_id"] = current_session_id.get() or ""
            request = request.override(args=args)
        return await handler(request)


_HIDDEN_SCOPE_ARGS = ("user_id", "session_id")


def _server_parameters() -> StdioServerParameters:
    """Build stdio params that spawn an unbound MCP server subprocess."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=str(_PROJECT_ROOT),
        env={**os.environ, "MCP_USER_ID": ""},
    )


def _scrub_agent_visible_tool(tool: BaseTool) -> tuple[Any, str]:
    """Hide scope args (user_id/session_id) from the agent-facing tool surface.

    Returns a (args_schema, description) pair with the scope args removed from
    the JSON schema (properties + required) and from the ``Args:`` block of the
    description. The underlying tool keeps the full schema so the interceptor's
    injected scope values still pass server-side validation.
    """
    schema = tool.args_schema
    if isinstance(schema, dict):
        schema = dict(schema)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            schema["properties"] = {k: v for k, v in properties.items() if k not in _HIDDEN_SCOPE_ARGS}
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [k for k in required if k not in _HIDDEN_SCOPE_ARGS]
    description = tool.description or ""
    lines = description.splitlines()
    scrubbed = [line for line in lines if not any(line.strip().startswith(f"{arg}:") for arg in _HIDDEN_SCOPE_ARGS)]
    return schema, "\n".join(scrubbed)


def _wrap_with_reconnect(tool: BaseTool, connection: "MCPToolConnection") -> StructuredTool:
    """Wrap an MCP tool so a dead session is restarted once and the call retried."""
    inner = tool
    args_schema, description = _scrub_agent_visible_tool(inner)

    async def _retry_once(**kwargs: Any) -> Any:
        start = time.perf_counter()
        logger.info("mcp_tool_call_started", tool_name=inner.name)
        success = False
        try:
            try:
                result = await inner.ainvoke(kwargs)
            except Exception:
                logger.warning("mcp_tool_call_failed_resetting", tool_name=inner.name)
                replacement = await connection.reset(inner.name)
                result = await replacement.ainvoke(kwargs)
            success = True
            return result
        finally:
            logger.info(
                "mcp_tool_call_completed",
                tool_name=inner.name,
                success=success,
                elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
            )

    return StructuredTool.from_function(
        name=inner.name,
        description=description or "",
        args_schema=args_schema,
        coroutine=_retry_once,
        response_format="content",
        metadata=inner.metadata,
    )


class MCPToolConnection:
    """A single persistent stdio session to the standalone MCP server.

    The stdio subprocess session must be entered and exited from the SAME
    task (anyio forbids crossing cancel-scope boundaries between tasks), so a
    long-lived owner task runs the ``stdio_client``/``ClientSession`` context
    for the connection's lifetime. Starters wait on a ready event; callers
    stop the connection by signalling the owner task and awaiting its exit.
    """

    _SERVER_NAME = "coding_helper"
    _SPAWN_TIMEOUT = 60.0

    def __init__(self) -> None:
        """Initialize an idle connection (nothing is spawned until start)."""
        self._session: ClientSession | None = None
        self._tools: list[BaseTool] | None = None
        self._interceptor = _ScopeForcingInterceptor()
        self._start_lock = asyncio.Lock()
        self._reset_lock = asyncio.Lock()
        self._reconnecting: asyncio.Task[list[BaseTool]] | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._stop_event: asyncio.Event | None = None
        self._start_error: BaseException | None = None

    @property
    def is_running(self) -> bool:
        """Return whether the MCP subprocess session is currently open."""
        return self._session is not None

    async def start(self) -> list[BaseTool]:
        """Spawn the MCP subprocess and load its tools (idempotent)."""
        if self._session is not None:
            return self._tools or []
        async with self._start_lock:
            if self._session is not None:
                return self._tools or []
            self._ready = asyncio.Event()
            self._stop_event = asyncio.Event()
            self._start_error = None
            self._loop_task = asyncio.create_task(self._connection_loop())
            self._loop_task.add_done_callback(self._consume_loop_exception)
            await asyncio.wait_for(self._ready.wait(), timeout=self._SPAWN_TIMEOUT)
            if self._start_error is not None:
                raise self._start_error
            return self._tools or []

    def _consume_loop_exception(self, task: asyncio.Task[None]) -> None:
        """Swallow a dead owner task's exception so it isn't reported unretrieved."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("mcp_tool_connection_loop_task_exception", error=str(exc))

    async def _connection_loop(self) -> None:
        """Owner task: holds the stdio context for the connection's lifetime."""
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream = await stack.enter_async_context(stdio_client(_server_parameters()))
                session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
                await session.initialize()
                self._session = session
                self._tools = await self._load_tools(session)
                logger.info("mcp_tool_connection_started", tool_count=len(self._tools))
                if self._ready is not None:
                    self._ready.set()
                assert self._stop_event is not None
                await self._stop_event.wait()
        except BaseException as e:
            if self._ready is not None and not self._ready.is_set():
                self._start_error = e
                self._ready.set()
            elif self._session is not None:
                logger.warning("mcp_tool_connection_loop_error", error=str(e))
            raise
        finally:
            self._session = None
            self._tools = None
            self._loop_task = None

    async def get_tools(self) -> list[BaseTool]:
        """Return the wrapped tools, starting the connection on first use."""
        if self._tools is None:
            return await self.start()
        return self._tools

    async def reset(self, tool_name: str) -> BaseTool:
        """Restart the MCP subprocess and return a fresh wrapper for a tool.

        Concurrent callers share a single restart: whichever detects the
        failure first reopens the session; the rest await the same reopen.
        """
        await self._ensure_reopened()
        replacement = next((tool for tool in (self._tools or []) if tool.name == tool_name), None)
        if replacement is None:
            raise _ToolNotFoundError(f"tool unavailable after MCP restart: {tool_name}")
        return replacement

    async def restart(self) -> None:
        """Close and reopen the MCP subprocess (used by agent graph resets)."""
        await self._ensure_reopened()

    async def _ensure_reopened(self) -> None:
        async with self._reset_lock:
            if self._reconnecting is None or self._reconnecting.done():
                self._reconnecting = asyncio.create_task(self._reopen())
            task = self._reconnecting
        await asyncio.shield(task)

    async def _reopen(self) -> list[BaseTool]:
        try:
            await self.stop()
            return await self.start()
        finally:
            self._reconnecting = None

    async def stop(self) -> None:
        """Close the MCP subprocess and drop cached tools."""
        loop_task = self._loop_task
        stop_event = self._stop_event
        if loop_task is None or stop_event is None:
            return
        stop_event.set()
        try:
            await loop_task
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("mcp_tool_connection_stop_failed")
        self._session = None
        self._tools = None
        logger.info("mcp_tool_connection_stopped")

    async def _load_tools(self, session: ClientSession) -> list[BaseTool]:
        all_tools = await load_mcp_tools(
            session,
            tool_interceptors=[self._interceptor],
            server_name=self._SERVER_NAME,
            tool_name_prefix=True,
            handle_tool_errors=True,
        )
        allowed = set(settings.MCP_AGENT_TOOLS)
        selected: list[BaseTool] = []
        for tool in all_tools:
            base_name = tool.name.removeprefix(f"{self._SERVER_NAME}_")
            if base_name in allowed and base_name not in ("server_status",):
                selected.append(_wrap_with_reconnect(tool, self))
        return selected


class _ToolNotFoundError(RuntimeError):
    """Raised when a tool is missing from the freshly restarted MCP session."""
