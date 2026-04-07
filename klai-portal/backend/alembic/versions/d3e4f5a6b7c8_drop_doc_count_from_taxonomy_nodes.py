"""Drop doc_count column from portal_taxonomy_nodes (SPEC-KB-027 R3).

doc_count was a denormalized counter that was only updated on node deletion
and never on re-ingest, backfill, or connector cleanup. The coverage dashboard
already fetches live chunk counts from Qdrant via the coverage-stats endpoint,
making this column misleading and unnecessary.

Revision ID: d3e4f5a6b7c8
Revises: c3d4e5f6a7b9
Create Date: 2026-04-07
"""

import sqlalchemy as sa
from alembic import op

revision = "d3e4f5a6b7c8"
down_revision = "c3d4e5f6a7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("portal_taxonomy_nodes", "doc_count")


def downgrade() -> None:
    op.add_column(
        "portal_taxonomy_nodes",
        sa.Column(
            "doc_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
