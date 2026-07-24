"""Observability module for the application."""

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.core.config import settings
from app.core.logging import logger


# Module-level Langfuse client — persists for the lifetime of the process.
# Required because Langfuse batches traces and flushes asynchronously;
# a local variable in langfuse_init() would be garbage-collected.
langfuse_client = None

# Deferred callback handler — created in langfuse_init() after the client
# is properly configured, NOT at import time.  Creating a CallbackHandler
# before Langfuse() is configured captures an uninitialised client singleton,
# which means traces are silently dropped.
langfuse_callback_handler = None


def langfuse_init():
    """Initialize Langfuse."""
    global langfuse_client, langfuse_callback_handler

    if not settings.LANGFUSE_TRACING_ENABLED:
        logger.debug("langfuse_tracing_disabled")
        return

    langfuse_client = Langfuse(
        tracing_enabled=settings.LANGFUSE_TRACING_ENABLED,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
        environment=settings.ENVIRONMENT.value,
        debug=settings.DEBUG,
    )

    # Create the callback handler AFTER the client is fully configured
    langfuse_callback_handler = CallbackHandler()

    try:
        if langfuse_client.auth_check():
            logger.debug("langfuse_auth_success")
        else:
            logger.warning("langfuse_auth_failure")
    except Exception:
        logger.exception("langfuse_auth_check_failed")


def get_langfuse_callback_handler() -> CallbackHandler:
    """Return the module-level Langfuse CallbackHandler.

    The handler is created lazily in ``langfuse_init()``, so this function
    must only be called after initialization has completed.
    """
    if langfuse_callback_handler is None:
        logger.warning("langfuse_callback_handler_accessed_before_init")
        # If called before init, the Langfuse singleton may not be
        # configured.  We still create a handler so the application doesn't
        # crash, but traces may be silently dropped until langfuse_init()
        # is called and overwrites this instance.
        return CallbackHandler()
    return langfuse_callback_handler


def flush_langfuse() -> None:
    """Flush pending Langfuse traces to the server.

    Call this on application shutdown to ensure all traces are sent.
    """
    if langfuse_client is not None:
        try:
            langfuse_client.flush()
            logger.debug("langfuse_flush_complete")
        except Exception:
            logger.exception("langfuse_flush_failed")
