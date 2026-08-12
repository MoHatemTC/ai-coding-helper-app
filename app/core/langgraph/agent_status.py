"""User-facing agent activity mapping and callback for live stream statuses.

The graph's nodes and tools are internal system steps (guardrails, intent
detection, async bookkeeping, ...). This module maps them to simple, friendly
activity codes so the chat UI can show what the agent is doing — thinking,
using a tool, processing files — without exposing internals.
"""

import asyncio
from typing import Any, Optional, override

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables.config import RunnableConfig

NODE_STATUS: dict[str, str] = {
    "secret_guardrail": "processing_prompt",  # pragma: allowlist secret
    "inbound_intent": "processing_prompt",
    "document_pipeline": "processing_files",
    "context_retrieval": "searching_code",
    "agent": "thinking",
    "model": "thinking",
    "generate_hint": "thinking",
}

IGNORED_NODES: frozenset[str] = frozenset(
    {
        "outbound",
        "store_messages",
        "summarization",
    }
)


def attach_callbacks(config: RunnableConfig, callbacks: list[BaseCallbackHandler]) -> RunnableConfig:
    """Append extra callback handlers to a run config's callback list.

    Args:
        config: The run config to update.
        callbacks: Handlers to append to the config's existing callbacks.

    Returns:
        The same config, now carrying the extra handlers.
    """
    if not callbacks:
        return config
    existing = config.get("callbacks")
    if isinstance(existing, list):
        merged: list[BaseCallbackHandler] = existing + list(callbacks)
    elif existing is not None:
        merged = list(existing.handlers) + list(callbacks)
    else:
        merged = list(callbacks)
    config["callbacks"] = merged
    return config


class AgentStatusCallback(BaseCallbackHandler):
    """Push user-facing activity codes to an ``asyncio.Queue`` as the graph runs.

    The queue is the per-request channel that the streaming endpoint drains to
    emit live status events. The handler methods are synchronous because
    LangGraph runs the graph on the event loop, so ``put_nowait`` happens on
    the same thread as the queue consumer.
    """

    def __init__(self, queue: "asyncio.Queue[tuple[str, Optional[str]]]") -> None:
        """Store the queue statuses are pushed to.

        Args:
            queue: The asyncio.Queue the streaming endpoint drains.
        """
        self._queue = queue

    @override
    def on_chain_start(self, serialized: Any, inputs: Any, **kwargs: Any) -> None:
        """Map a node chain start to its user-facing status."""
        self._emit_node(kwargs)

    @override
    def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        """Map a chat model call inside a node to its user-facing status."""
        self._emit_node(kwargs)

    @override
    def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
        """Report the tool the agent is about to use."""
        tool_name = kwargs.get("name") or (serialized or {}).get("name")
        self._put("using_tool", str(tool_name) if tool_name else None)

    def _emit_node(self, kwargs: dict[str, Any]) -> None:
        """Emit the status mapped from the current node, when user-facing."""
        metadata = kwargs.get("metadata") or {}
        node = metadata.get("langgraph_node")
        if not node or node in IGNORED_NODES:
            return
        status = NODE_STATUS.get(node)
        if status:
            self._put(status, None)

    def _put(self, status: str, tool_name: Optional[str]) -> None:
        """Push a status to the queue, never letting callback errors leak."""
        try:
            self._queue.put_nowait((status, tool_name))
        except Exception:
            pass
