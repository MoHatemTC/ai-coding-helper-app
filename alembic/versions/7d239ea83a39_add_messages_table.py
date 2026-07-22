"""add_messages_table.

Revision ID: 7d239ea83a39
Revises: b25d38b0cd7c
Create Date: 2026-07-22 19:57:50.108167

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel  # noqa: F401

from alembic import op
import sqlmodel.sql
import sqlmodel.sql.sqltypes

# revision identifiers, used by Alembic.
revision: str = "7d239ea83a39"
down_revision: Union[str, Sequence[str], None] = "b25d38b0cd7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "message",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("role", sqlmodel.sql.sqltypes.AutoString(length=10), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_messages_session_id", "message", ["session_id", "created_at"])
    op.create_index("idx_messages_user_id", "message", ["user_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_messages_user_id", table_name="message")
    op.drop_index("idx_messages_session_id", table_name="message")
    op.drop_table("message")
