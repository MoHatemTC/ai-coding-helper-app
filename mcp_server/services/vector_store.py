"""Standalone pgvector vector store for the MCP server (no app imports).

Ports the semantic code-chunk search from ``app.services.vector_store`` /
``app.mcp.chunk_code_server`` using its own engine and embedding model, plus
an ownership pre-check against the ``session`` table.
"""

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text
from sqlmodel import Session

from mcp_server.config import config


@dataclass
class Chunk:
    """A searchable code chunk returned to tools."""

    file_name: str
    language: str
    content: str
    created_at: datetime | None = None


_engine = None
_embedder: SentenceTransformer | None = None
_embedder_lock = threading.Lock()


def _get_engine():
    """Get a lazily-created SQLAlchemy engine for the standalone server."""
    global _engine
    if _engine is None:
        connection_url = (
            f"postgresql://{config.postgres_user}:{config.postgres_password}"
            f"@{config.postgres_host}:{config.postgres_port}/{config.postgres_db}"
        )
        _engine = create_engine(connection_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return _engine


def _get_embedder() -> SentenceTransformer:
    """Get the lazily-loaded sentence-transformers embedding model.

    The model is loaded once (guarded by a lock so concurrent first calls do
    not load it twice) with stdout fd 1 suppressed: the C/Rust-backed loader
    writes directly to the process's stdout file descriptor on first load,
    bypassing Python's ``sys.stdout``, which would corrupt the stdio JSON-RPC
    channel. Model loading is the only known stdout writer, so suppression is
    narrowed to this one-time step instead of every embed call.
    """
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                with suppress_stdout_fd():
                    _embedder = SentenceTransformer(config.embedding_model_name)
    return _embedder


def warmup_embedder() -> None:
    """Force the embedding model to load before the stdio loop starts."""
    _get_embedder()


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of text strings."""
    model = _get_embedder()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [embedding.tolist() for embedding in embeddings]


@contextmanager
def suppress_stdout_fd():
    """Redirect file descriptor 1 to devnull, restoring it on exit.

    Some C/Rust libraries (e.g. sentence-transformers' tqdm on first model
    load) write directly to the process's stdout file descriptor, bypassing
    Python's ``sys.stdout``. That corrupts the stdio JSON-RPC channel, so the
    fd is swapped for the duration of the one-time model load only.
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


def session_belongs_to_user(session_id: str, user_id: int) -> bool:
    """Return whether a session exists and is owned by the given user.

    This is the Layer 1 ownership backstop: a mismatched ``(session_id,
    user_id)`` pair (which the code_chunk WHERE clause alone cannot detect
    when both values are supplied) is rejected up front.
    """
    try:
        with Session(_get_engine()) as session:
            row = session.execute(
                text("SELECT 1 FROM session WHERE id = :session_id AND user_id = :user_id"),
                {"session_id": session_id, "user_id": user_id},
            ).first()
        return row is not None
    except Exception:
        return False


def search_code_chunks(
    query: str,
    session_id: str,
    user_id: int,
    file_name: str | None = None,
    top_k: int | None = None,
) -> list[Chunk]:
    """Search code chunks by cosine similarity, scoped to a session and user.

    Args:
        query: The natural-language query.
        session_id: Scope results to this session (required).
        user_id: Scope results to this user (required).
        file_name: If set, scope results to this filename.
        top_k: Number of results (defaults to ``config.top_k_retrieval``).

    Returns:
        A list of Chunk records ordered by similarity (most similar first).
    """
    k = top_k or config.top_k_retrieval

    query_vec = _embed([query])[0]

    conditions = ["ch.session_id = :session_id", "ch.user_id = :user_id"]
    params: dict = {"k": k, "session_id": session_id, "user_id": user_id}

    if file_name is not None:
        conditions.append("ch.file_name = :file_name")
        params["file_name"] = file_name

    where_clause = " AND ".join(conditions)

    stmt = text(
        f"""
        SELECT ch.file_name, ch.language, ch.content, ch.created_at,
               (ch.embedding <=> CAST(:query_vec AS vector)) AS distance
        FROM code_chunk ch
        WHERE {where_clause}
        ORDER BY distance ASC
        LIMIT :k
        """
    )
    params["query_vec"] = query_vec

    with Session(_get_engine()) as session:
        rows = session.execute(stmt, params).fetchall()

    results: list[Chunk] = []
    for row in rows:
        mapping = row._mapping
        results.append(
            Chunk(
                file_name=mapping["file_name"],
                language=mapping["language"],
                content=mapping["content"],
                created_at=mapping["created_at"],
            )
        )
    return results
