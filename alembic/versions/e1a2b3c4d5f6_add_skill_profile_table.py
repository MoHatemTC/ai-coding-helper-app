"""Add skillprofileentry table.

Revision ID: e1a2b3c4d5f6
Revises: 7d239ea83a39
Create Date: 2026-07-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1a2b3c4d5f6"  # pragma: allowlist secret
down_revision: Union[str, None] = "7d239ea83a39"  # pragma: allowlist secret
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the skillprofileentry table."""
    op.create_table(
        "skillprofileentry",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("skill", sa.Text(), nullable=False),
        sa.Column("skill_key", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("proficiency", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skillprofileentry_user_id", "skillprofileentry", ["user_id"])
    op.create_index("ix_skillprofileentry_skill_key", "skillprofileentry", ["skill_key"])


def downgrade() -> None:
    """Drop the skillprofileentry table."""
    op.drop_index("ix_skillprofileentry_skill_key", table_name="skillprofileentry")
    op.drop_index("ix_skillprofileentry_user_id", table_name="skillprofileentry")
    op.drop_table("skillprofileentry")
