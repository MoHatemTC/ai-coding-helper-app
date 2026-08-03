"""FastMCP server exposing the AI coding assistant's capabilities as MCP tools.

Tools:
- ``search_code``: semantic search over uploaded code chunks (pgvector).
- ``web_search``: DuckDuckGo web search.
- ``ask_agent``: run the full LangGraph ReActAgent (guardrails, memory,
  document pipeline, summarization) for a given session/user.

Run via SSE (recommended for production)::

    uv run python -m app.mcp.chunk_code_server --transport sse

Then connect from an MCP client to http://localhost:8100/sse

Run via stdio (for LangChain MCP adapter or local clients)::

    uv run python -m app.mcp.chunk_code_server --transport stdio
"""

import asyncio
import logging
import os
from argparse import ArgumentParser
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, override

from mcp.server.fastmcp import FastMCP
from psycopg import (
    Error as PsycopgError,
    InterfaceError,
    OperationalError as PsycopgOperationalError,
)
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from sqlmodel import Session as SASession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.langgraph.ReAct_agent_graph import (
    AgentDatabaseUnavailableError,
    ReActAgent,
)
from app.core.langgraph.tools.duckduckgo_search import duckduckgo_search_tool
from app.core.logging import logger
from app.schemas import Message
from app.services.database import database_service

_ddg = duckduckgo_search_tool

logging.getLogger("mcp").setLevel(logging.INFO)

_agent = ReActAgent()

DB_UNAVAILABLE_MSG = (
    "Error: Database service is unavailable. Please ensure PostgreSQL is running "
    "(`make docker-up` / `make stack-up`) and try again."
)

_embedder: SentenceTransformer | None = None
_engine = None


def _get_engine():
    """Get a SQLAlchemy engine."""
    global _engine
    if _engine is None:
        connection_url = (
            f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
            f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        )
        _engine = create_engine(connection_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _engine


def _get_embedder() -> SentenceTransformer:
    """Get a SentenceTransformer instance."""
    global _embedder
    if _embedder is None:
        logger.info("mcp_loading_embedding_model", model=settings.EMBEDDING_MODEL_NAME)
        _embedder = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of text strings."""
    model = _get_embedder()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]


@contextmanager
def _suppress_stdout_fd():
    """Redirect file descriptor 1 to devnull, restoring it on exit.

    Some C/Rust libraries (e.g. primp used by ddgs) write directly to the
    process's stdout file descriptor, bypassing Python's ``sys.stdout``.
    This is unsafe on stdio transport, where every stdout line must be a
    JSON-RPC message, so the fd is swapped for the duration of the call.
    """
    saved_fd = os.dup(1)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 1)
        yield
    finally:
        os.dup2(saved_fd, 1)
        os.close(devnull_fd)
        os.close(saved_fd)


@asynccontextmanager
async def _mcp_lifespan(_: FastMCP) -> AsyncIterator[None]:
    """Pre-warm the LangGraph agent so the first tool call has no cold start.

    DB failures here are non-fatal: the server keeps running and tools
    self-heal (retry graph/pool init) once PostgreSQL is back.
    """
    try:
        await _agent.create_graph()
        logger.info("mcp_agent_graph_pre_warmed")
    except (AgentDatabaseUnavailableError, PsycopgError) as e:
        logger.warning("mcp_agent_graph_pre_warm_db_unavailable", error=str(e))
    except Exception:
        logger.exception("mcp_agent_graph_pre_warm_failed")
    yield


mcp = FastMCP(
    "ai-coding-assistant",
    instructions="AI coding assistant. Tools: search_code (semantic search over "
    "uploaded code chunks, scoped to a session/user), web_search (DuckDuckGo), "
    "ask_agent (run the full agent for a message in a session).",
    lifespan=_mcp_lifespan,
)


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests without the configured MCP bearer token (SSE/HTTP only)."""

    @override
    async def dispatch(self, request, call_next):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {settings.MCP_AUTH_TOKEN}":
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)


@mcp.tool()
def search_code(query: str, session_id: str, user_id: int, file_name: str | None = None) -> str:
    """Search uploaded code files for relevant chunks.

    Args:
        query: Natural-language description of the code to find.
        session_id: The session to scope results to.
        user_id: The user who owns the session.
        file_name: Optional — narrow to a specific filename.

    Returns:
        Formatted code chunks with file info, or a no-results message.
    """
    logger.info(
        "mcp_search_code_invoked",
        session_id=session_id,
        user_id=user_id,
        file_name=file_name,
        query=query,
    )

    if user_id < 1 or not session_id or not query.strip():
        return "Error: invalid arguments — query, session_id, and a positive user_id are required."

    try:
        # Model loading (sentence-transformers) writes a tqdm progress bar to
        # stdout, which would corrupt the stdio JSON-RPC channel. Suppress fd 1
        # for the duration of embedding + query.
        with _suppress_stdout_fd():
            k = getattr(settings, "TOP_K_RETRIEVAL", 5)
            query_vec = _embed([query])[0]

            conditions = ["ch.session_id = :session_id", "ch.user_id = :user_id"]
            params: dict = {"k": k, "session_id": session_id, "user_id": user_id}

            if file_name is not None:
                conditions.append("ch.file_name = :file_name")
                params["file_name"] = file_name

            where_clause = " AND ".join(conditions)

            stmt = text(
                f"""
                SELECT ch.id, ch.user_id, ch.session_id, ch.file_id,
                       ch.file_name, ch.language, ch.content,
                       ch.embedding, ch.created_at,
                       (ch.embedding <=> CAST(:query_vec AS vector)) AS distance
                FROM code_chunk ch
                WHERE {where_clause}
                ORDER BY distance ASC
                LIMIT :k
                """
            )
            params["query_vec"] = query_vec

            engine = _get_engine()
            with SASession(engine) as session:
                rows = session.execute(stmt, params).fetchall()

            if not rows:
                return "No matching code found."

            lines: list[str] = []
            for row in rows:
                m = row._mapping
                ts = m.get("created_at")
                created = ts.isoformat() if ts else "unknown"
                header = f"[{m['file_name']} ({m['language']})] created: {created}"
                lines.append(f"{header}\n```\n{m['content']}\n```")

            return "\n---\n".join(lines)
    except (SqlAlchemyOperationalError, PsycopgOperationalError) as e:
        logger.warning("mcp_search_code_db_unavailable", error=str(e))
        return DB_UNAVAILABLE_MSG
    except Exception as e:
        logger.exception("mcp_search_code_failed", error=str(e))
        return "Error: code search failed unexpectedly. Please try again."


@mcp.tool()
def web_search(query: str) -> str:
    """Search the web using DuckDuckGo.

    Args:
        query: The search query.

    Returns:
        Search results as a string.
    """
    logger.info("mcp_web_search_invoked", query=query)
    with _suppress_stdout_fd():
        return _ddg.run(query)


@mcp.tool()
async def ask_agent(message: str, session_id: str, user_id: int, username: str | None = None) -> str:
    """Ask the AI coding assistant agent a question.

    Runs the full LangGraph agent: secret guardrail, intent classification,
    document pipeline, ReAct reasoning with code/web search, outbound
    guardrail, message storage, and summarization. Uses the agent's
    long-term memory and skill profile for the user.

    Args:
        message: The user's question or instruction.
        session_id: Conversation thread to run this turn in (history is
            loaded from the checkpointer and appended to).
        user_id: The user who owns the session (scopes memory + files).
        username: Optional display name used in the system prompt.

    Returns:
        The assistant's reply text.
    """
    logger.info("mcp_ask_agent_invoked", session_id=session_id, user_id=user_id, username=username)

    if user_id < 1 or not session_id or not message.strip():
        return "Error: invalid arguments — message, session_id, and a positive user_id are required."

    user_message = Message(role="user", content=message)
    max_attempts = settings.MCP_DB_RETRY_MAX_ATTEMPTS

    for attempt in range(1, max_attempts + 1):
        try:
            if not await database_service.health_check():
                raise AgentDatabaseUnavailableError("database health check failed")

            # The agent's memory service loads a sentence-transformers model
            # whose tqdm progress bar would pollute stdio stdout. Suppress fd 1.
            with _suppress_stdout_fd():
                responses = await _agent.get_response(
                    user_message,
                    session_id=session_id,
                    user_id=str(user_id),
                    username=username,
                )
            if not responses:
                return "No response generated."
            return "\n".join(response.content for response in responses)
        except (AgentDatabaseUnavailableError, PsycopgOperationalError, InterfaceError) as exc:
            logger.warning(
                "mcp_ask_agent_db_unavailable",
                attempt=attempt,
                max_attempts=max_attempts,
                error=str(exc),
            )
            if attempt < max_attempts:
                await _agent.reset_graph()
                await asyncio.sleep(settings.MCP_DB_RETRY_BACKOFF_SECONDS * attempt)
        except Exception as exc:
            logger.exception("mcp_ask_agent_failed", error=str(exc))
            return f"Error: the agent could not process the request: {exc}"

    return DB_UNAVAILABLE_MSG


def _run_http(transport: str, host: str, port: int) -> None:
    """Run the HTTP transports (sse / streamable-http) with optional auth."""
    import uvicorn

    if transport == "sse":
        app = mcp.sse_app()
    else:
        app = mcp.streamable_http_app()

    if settings.MCP_AUTH_TOKEN:
        app.add_middleware(_BearerAuthMiddleware)

    logger.info("mcp_server_starting", transport=transport, host=host, port=port, auth=bool(settings.MCP_AUTH_TOKEN))
    uvicorn.run(app, host=host, port=port, log_level="warning")


def main() -> None:
    """Run the server."""
    parser = ArgumentParser(description="MCP AI coding assistant server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=settings.MCP_SERVER_TRANSPORT,
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument("--host", default=settings.MCP_SERVER_HOST, help=f"Host (default: {settings.MCP_SERVER_HOST})")
    parser.add_argument(
        "--port", type=int, default=settings.MCP_SERVER_PORT, help=f"Port (default: {settings.MCP_SERVER_PORT})"
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        logger.info("mcp_server_starting", transport="stdio")
        mcp.run(transport="stdio")
    else:
        _run_http(args.transport, args.host, args.port)


if __name__ == "__main__":
    main()
