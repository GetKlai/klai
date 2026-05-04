"""Drop doc_count from portal_taxonomy_nodes (R3 from closed PR #90).

Revision ID: fd9c4a39d14b
Revises: ed5b78b296f5
Create Date: 2026-05-04

The denormalized doc_count column was only updated on delete/merge, so it
was stale on every re-ingest or backfill. Frontend never rendered it (only
defined as TS type). Live counts, if needed, are queried from Qdrant via
the coverage dashboard — a separate concern not handled here.

Down-migration recreates the column without backfill (intentional: any
post-deploy use of doc_count in restored code will show 0 until the next
explicit aggregation run).
"""

import sqlalchemy as sa
from alembic import op

revision = "fd9c4a39d14b"
down_revision = "ed5b78b296f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("portal_taxonomy_nodes", "doc_count")


def downgrade() -> None:
    op.add_column(
        "portal_taxonomy_nodes",
        sa.Column("doc_count", sa.Integer(), nullable=False, server_default="0"),
    )
