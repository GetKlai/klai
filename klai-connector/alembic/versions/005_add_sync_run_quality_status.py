"""Add quality_status column to connector.sync_runs.

SPEC-CRAWL-003 REQ-2: New nullable quality_status column for three-layer
content quality guardrails. Allowed values: 'healthy' | 'degraded' | 'failed' | NULL.
NULL on existing historical rows (no backfill — REQ-19).

Revision ID: 005_add_sync_run_quality_status
Revises: 004_remove_sync_run_fk
Create Date: 2026-04-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_add_sync_run_quality_status"
down_revision: str | None = "004_remove_sync_run_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Pure DDL: add nullable column. No default, no backfill — existing rows keep NULL.
    # No index: quality_status is low-cardinality and queried alongside connector_id
    # which is already indexed. SPEC-CRAWL-003 Data Model Diff.
    op.add_column(
        "sync_runs",
        sa.Column("quality_status", sa.String(20), nullable=True),
        schema="connector",
    )


def downgrade() -> None:
    op.drop_column("sync_runs", "quality_status", schema="connector")
