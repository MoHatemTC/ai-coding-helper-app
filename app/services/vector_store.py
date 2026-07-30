"""Vector store service: embed chunks, store in pgvector, search, and clean up."""

from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.logging import logger
from app.models.code_chunk import CodeChunk
from app.services.database import database_service


_embedder: SentenceTransformer | None = None


def _get_embedder() -> SentenceTransformer:
    """Lazy-load the embedding model singleton."""
    global _embedder
    if _embedder is None:
        logger.info("loading_embedding_model", model=settings.EMBEDDING_MODEL_NAME)
        _embedder = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a list of text strings.

    Args:
        texts: The text strings to embed.

    Returns:
        A list of embedding vectors (each a list of floats).
    """
    model = _get_embedder()
    embeddings = model.encode(texts, show_progress_bar=False)
    return [emb.tolist() for emb in embeddings]


class VectorStoreService:
    """Service for storing, searching, and managing code chunk vectors."""

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    @staticmethod
    def store_chunks(
        chunks: list[CodeChunk],
    ) -> None:
        """Embed chunk content and persist to the code_chunks table.

        Args:
            chunks: CodeChunk records to store (embedding field is populated here).
        """
        contents = [c.content for c in chunks]
        vectors = _embed(contents)

        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector

        with Session(database_service.engine) as session:
            session.add_all(chunks)
            session.commit()

        logger.info(
            "chunks_stored",
            count=len(chunks),
            file_id=chunks[0].file_id if chunks else None,
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @staticmethod
    def search(
        query: str,
        session_id: str,
        user_id: int,
        file_name: str | None = None,
        top_k: int | None = None,
    ) -> list[CodeChunk]:
        """Search chunks by cosine similarity to a query string.

        Always scoped to the current session and user.

        Args:
            query: The natural-language query.
            session_id: Scope results to this session (required).
            user_id: Scope results to this user (required).
            file_name: If set, scope results to this filename.
            top_k: Number of results (defaults to settings.TOP_K_RETRIEVAL).

        Returns:
            A list of CodeChunk records ordered by similarity (most similar first).
        """
        k = top_k or settings.TOP_K_RETRIEVAL
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
                   ch.file_name, ch.language, ch.content, ch.chunk_metadata,
                   ch.embedding, ch.created_at,
                   (ch.embedding <=> :query_vec::vector) AS distance
            FROM code_chunk ch
            WHERE {where_clause}
            ORDER BY distance ASC
            LIMIT :k
            """
        )
        params["query_vec"] = query_vec

        with Session(database_service.engine) as session:
            rows = session.execute(stmt, params).fetchall()

        results: list[CodeChunk] = []
        for row in rows:
            results.append(
                CodeChunk(
                    id=row.id,
                    user_id=row.user_id,
                    session_id=row.session_id,
                    file_id=row.file_id,
                    file_name=row.file_name,
                    language=row.language,
                    content=row.content,
                    chunk_metadata=row.chunk_metadata or {},
                    embedding=row.embedding,
                    created_at=row.created_at,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Full-file retrieval
    # ------------------------------------------------------------------

    @staticmethod
    def get_chunks_by_file(file_id: str) -> list[CodeChunk]:
        """Retrieve all chunks belonging to a specific file.

        Args:
            file_id: The file identifier.

        Returns:
            A list of CodeChunk records ordered by created_at ASC.
        """
        with Session(database_service.engine) as session:
            statement = (
                select(CodeChunk).where(col(CodeChunk.file_id) == file_id).order_by(col(CodeChunk.created_at).asc())
            )
            results = session.exec(statement).all()
            return list(results)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @staticmethod
    def delete_chunks_by_file(file_id: str) -> None:
        """Delete all chunks for a file.

        Args:
            file_id: The file whose chunks to delete.
        """
        with Session(database_service.engine) as session:
            statement = select(CodeChunk).where(col(CodeChunk.file_id) == file_id)
            chunks = session.exec(statement).all()
            for chunk in chunks:
                session.delete(chunk)
            session.commit()
            logger.info("chunks_deleted", file_id=file_id, count=len(chunks))

    @staticmethod
    def delete_chunks_by_session(session_id: str) -> None:
        """Delete all chunks for a session.

        Args:
            session_id: The session whose chunks to delete.
        """
        with Session(database_service.engine) as session:
            statement = select(CodeChunk).where(col(CodeChunk.session_id) == session_id)
            chunks = session.exec(statement).all()
            for chunk in chunks:
                session.delete(chunk)
            session.commit()
            logger.info(
                "session_chunks_deleted",
                session_id=session_id,
                count=len(chunks),
            )


vector_store_service = VectorStoreService()
