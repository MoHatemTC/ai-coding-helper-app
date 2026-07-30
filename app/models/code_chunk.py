"""Code chunk model for storing AST-based code chunks with embeddings."""

from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSON
from sqlmodel import Field

from app.core.config import settings
from app.models.base import BaseModel


class CodeChunk(BaseModel, table=True):
    """A chunk of code produced by the document pipeline's AST splitter.

    Attributes:
        id: The primary key (UUID)
        user_id: Foreign key to the user who owns the file
        session_id: Foreign key to the session the file was uploaded in
        file_id: Unique identifier per uploaded file
        file_name: Original filename, LLM-filterable
        language: Detected programming language
        content: The chunk text extracted by the code splitter
        embedding: pgvector embedding vector (768d)
        chunk_metadata: JSONB blob from llamaIndex CodeSplitter (start_line, end_line, etc.)
        created_at: When the chunk was created (inherited from BaseModel)
    """

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: int = Field(foreign_key="user.id", nullable=False)
    session_id: str = Field(foreign_key="session.id", nullable=False)
    file_id: str = Field(nullable=False, index=True)
    file_name: str = Field(nullable=False, index=True)
    language: str = Field(nullable=False, index=True)
    content: str = Field(sa_column=Column(Text, nullable=False))
    embedding: list[float] | None = Field(
        default=None, sa_column=Column(Vector(settings.EMBEDDING_DIM), nullable=True)
    )
    chunk_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False, server_default="{}"))
