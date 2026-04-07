"""Add kb_slug to research.notebooks for taxonomy-aware retrieval

Revision ID: 0004_add_kb_slug
Revises: 0003_drop_embedding
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_add_kb_slug"
down_revision = "0003_drop_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notebooks",
        sa.Column("kb_slug", sa.VARCHAR(128), nullable=True),
        schema="research",
    )


def downgrade() -> None:
    op.drop_column("notebooks", "kb_slug", schema="research")
