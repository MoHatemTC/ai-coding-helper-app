"""FastMCP server exposing code search as an MCP tool.

Run via SSE (recommended for production)::

    uv run python -m app.mcp.server

Then connect from an MCP client to http://localhost:8100/sse

Run via stdio (for LangChain MCP adapter)::

    uv run python -m app.mcp.server --transport stdio
"""

from argparse import ArgumentParser

from mcp.server.fastmcp import FastMCP
from sentence_transformers import SentenceTransformer
from sqlalchemy import create_engine, text
from sqlmodel import Session as SASession

from app.core.config import settings
from app.core.logging import logger

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


mcp = FastMCP(
    "code-search",
    instructions="Search uploaded code files by semantic similarity. "
    "Provide a natural-language query describing what code you need. "
    "Results are scoped to a session and user.",
)


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


def main() -> None:
    """Run the server."""
    parser = ArgumentParser(description="MCP code-search server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="sse",
        help="Transport protocol (default: sse on port 8100)",
    )
    parser.add_argument("--port", type=int, default=8100, help="Port for SSE transport (default: 8100)")
    args = parser.parse_args()

    logger.info("mcp_server_starting", transport=args.transport, port=args.port)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse")


if __name__ == "__main__":
    main()
