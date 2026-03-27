"""add resolved_at to portal_retrieval_gaps

Revision ID: f8a9b0c1d2e3
Revises: a1b2c3d4e5f6
Create Date: 2026-03-27
"""

from alembic import op
import sqlalchemy as sa

revision = "f8a9b0c1d2e3"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portal_retrieval_gaps", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_retrieval_gaps_open",
        "portal_retrieval_gaps",
        ["org_id", "query_text"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_gaps_open", table_name="portal_retrieval_gaps")
    op.drop_column("portal_retrieval_gaps", "resolved_at")
