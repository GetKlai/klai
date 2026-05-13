"""add caller_client_id to portal_retrieval_gaps (SPEC-MCP-RETRIEVAL-001 Phase 2)

Optional ``caller_client_id`` column lets the gap-events stream
distinguish OAuth-client traffic (Claude Desktop / Cursor / ChatGPT) from
LibreChat traffic. ``NULL`` rows = LibreChat (the historic semantics).

Non-blocking ADD COLUMN with NULL default. PostgreSQL 11+ does this as
metadata-only; existing rows are not rewritten. No RLS policy changes
needed — ``portal_retrieval_gaps`` is already RLS Category-D, and adding
a column does not affect the policy expression.

Partial index (WHERE caller_client_id IS NOT NULL) is created
CONCURRENTLY in ``post_deploy_a5b8c2d6e1f3.sql`` because CONCURRENTLY
cannot run inside a transaction.

Revision ID: a5b8c2d6e1f3
Revises: e84a10348987
Create Date: 2026-05-07
"""

import sqlalchemy as sa
from alembic import op

revision = "a5b8c2d6e1f3"
down_revision = "e84a10348987"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_retrieval_gaps",
        sa.Column("caller_client_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_retrieval_gaps", "caller_client_id")
