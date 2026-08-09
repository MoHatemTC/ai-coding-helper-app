"""Observability module for the application."""

from typing import Any, cast

from langchain_core.callbacks import BaseCallbackManager
from langchain_core.runnables.config import RunnableConfig
from langfuse import Langfuse
from langfuse._client.get_client import get_client
from langfuse.langchain import CallbackHandler

from app.core.config import settings
from app.core.logging import logger


def langfuse_init():
    """Initialize Langfuse."""
    if not settings.LANGFUSE_TRACING_ENABLED:
        logger.debug("langfuse_tracing_disabled")
        return

    langfuse = Langfuse(
        tracing_enabled=settings.LANGFUSE_TRACING_ENABLED,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
        environment=settings.ENVIRONMENT.value,
        debug=settings.LANGFUSE_DEBUG,
    )

    try:
        if langfuse.auth_check():
            logger.debug("langfuse_auth_success")
        else:
            logger.warning("langfuse_auth_failure")
    except Exception:
        logger.exception("langfuse_auth_check_failed")


def get_langfuse_callback_handler() -> CallbackHandler:
    """Create a Langfuse CallbackHandler for tracking LLM interactions.

    Returns:
        CallbackHandler: Configured Langfuse callback handler.
    """
    return CallbackHandler()


def new_langfuse_callback_handler() -> CallbackHandler | None:
    """Create a per-request Langfuse callback handler, or None when tracing is off.

    Returns:
        A fresh CallbackHandler instance when tracing is enabled, else None.
    """
    if not settings.LANGFUSE_TRACING_ENABLED:
        return None
    return CallbackHandler()


langfuse_callback_handler = get_langfuse_callback_handler()


def build_langfuse_config(config: RunnableConfig | None) -> RunnableConfig:
    """Merge langfuse tracing callbacks and metadata from a graph config.

    Args:
        config: The runnable config from the graph run, or None.

    Returns:
        A runnable config with the langfuse handler in ``callbacks`` and the
        graph ``metadata`` preserved, so LLM calls made outside the graph's
        own callback propagation are still traced.
    """
    raw_callbacks = (config or {}).get("callbacks")

    if isinstance(raw_callbacks, BaseCallbackManager):
        callbacks: list = list(raw_callbacks.handlers)
    elif isinstance(raw_callbacks, list):
        callbacks = list(raw_callbacks)
    elif raw_callbacks is not None:
        callbacks = [raw_callbacks]
    else:
        callbacks = []

    if settings.LANGFUSE_TRACING_ENABLED and not any(isinstance(cb, CallbackHandler) for cb in callbacks):
        callbacks.append(langfuse_callback_handler)

    merged: dict[str, Any] = {}
    if callbacks:
        merged["callbacks"] = callbacks
    metadata = (config or {}).get("metadata")
    if metadata:
        merged["metadata"] = metadata
    return cast(RunnableConfig, merged)


def langfuse_flush() -> None:
    """Flush buffered Langfuse traces on application shutdown."""
    if not settings.LANGFUSE_TRACING_ENABLED:
        return
    try:
        get_client().flush()
    except Exception:
        logger.exception("langfuse_flush_failed")
