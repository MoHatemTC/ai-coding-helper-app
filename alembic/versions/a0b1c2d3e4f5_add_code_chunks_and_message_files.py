"""Add code_chunks table and message.files JSON column.

Revision ID: a0b1c2d3e4f5
Revises: e1a2b3c4d5f6
Create Date: 2026-07-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a0b1c2d3e4f5"  # pragma: allowlist secret
down_revision: Union[str, None] = "e1a2b3c4d5f6"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create code_chunks table and add files column to message."""
    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add files JSON column to message table
    op.add_column(
        "message",
        sa.Column("files", sa.JSON(), nullable=True),
    )

    # Create code_chunk table
    op.create_table(
        "code_chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "embedding",
            sa.types.UserDefinedType("vector(768)"),
            nullable=True,
        ),
        sa.Column("chunk_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index("idx_code_chunk_file_id", "code_chunk", ["file_id"])
    op.create_index("idx_code_chunk_file_name", "code_chunk", ["file_name"])
    op.create_index("idx_code_chunk_session_id", "code_chunk", ["session_id"])
    op.create_index("idx_code_chunk_user_id", "code_chunk", ["user_id"])
    op.create_index(
        "idx_code_chunk_embedding_hnsw",
        "code_chunk",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 200},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Drop code_chunks table and remove files column from message."""
    op.execute("DROP INDEX IF EXISTS idx_code_chunk_embedding_hnsw")
    op.drop_index("idx_code_chunk_user_id", table_name="code_chunk")
    op.drop_index("idx_code_chunk_session_id", table_name="code_chunk")
    op.drop_index("idx_code_chunk_file_name", table_name="code_chunk")
    op.drop_index("idx_code_chunk_file_id", table_name="code_chunk")
    op.drop_table("code_chunk")
    op.drop_column("message", "files")
