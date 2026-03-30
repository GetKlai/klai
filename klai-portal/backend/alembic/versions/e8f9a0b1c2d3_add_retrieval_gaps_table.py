"""Add portal_retrieval_gaps table.

Revision ID: e8f9a0b1c2d3
Revises: d6e7f8a9b0c1
Create Date: 2026-03-27
"""

import sqlalchemy as sa
from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_retrieval_gaps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("gap_type", sa.Text(), nullable=False),
        sa.Column("top_score", sa.Double(), nullable=True),
        sa.Column("nearest_kb_slug", sa.Text(), nullable=True),
        sa.Column(
            "chunks_retrieved",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "retrieval_ms",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "gap_type IN ('hard', 'soft')",
            name="ck_retrieval_gaps_gap_type",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retrieval_gaps_org_occurred",
        "portal_retrieval_gaps",
        ["org_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_retrieval_gaps_org_query",
        "portal_retrieval_gaps",
        ["org_id", "query_text"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_gaps_org_query")
    op.drop_index("ix_retrieval_gaps_org_occurred")
    op.drop_table("portal_retrieval_gaps")
