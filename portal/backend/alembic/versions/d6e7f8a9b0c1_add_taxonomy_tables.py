"""Add taxonomy tables for knowledge base categorisation.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-03-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_taxonomy_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kb_id",
            sa.Integer(),
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("portal_taxonomy_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), nullable=False),
    )
    op.create_index("ix_taxonomy_nodes_kb_id", "portal_taxonomy_nodes", ["kb_id"])
    op.create_index("ix_taxonomy_nodes_parent_id", "portal_taxonomy_nodes", ["parent_id"])

    # Partial unique indexes for sibling name uniqueness (handles NULL parent_id correctly)
    op.execute(
        "CREATE UNIQUE INDEX uq_taxonomy_nodes_sibling_name "
        "ON portal_taxonomy_nodes (kb_id, parent_id, name) "
        "WHERE parent_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_taxonomy_nodes_root_name ON portal_taxonomy_nodes (kb_id, name) WHERE parent_id IS NULL"
    )

    op.create_table(
        "portal_taxonomy_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kb_id",
            sa.Integer(),
            sa.ForeignKey("portal_knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposal_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "proposal_type IN ('new_node', 'merge', 'split', 'rename')",
            name="ck_taxonomy_proposal_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_taxonomy_proposal_status",
        ),
    )
    op.create_index("ix_taxonomy_proposals_kb_status", "portal_taxonomy_proposals", ["kb_id", "status"])


def downgrade() -> None:
    op.drop_table("portal_taxonomy_proposals")
    op.drop_index("uq_taxonomy_nodes_root_name", table_name="portal_taxonomy_nodes")
    op.drop_index("uq_taxonomy_nodes_sibling_name", table_name="portal_taxonomy_nodes")
    op.drop_table("portal_taxonomy_nodes")
