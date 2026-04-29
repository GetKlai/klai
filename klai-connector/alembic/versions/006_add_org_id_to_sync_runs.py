"""Add org_id column to connector.sync_runs (tenant scoping).

SPEC-SEC-TENANT-001 REQ-7.1 (v0.5.1): the ``connector.sync_runs`` table
gains an ``org_id VARCHAR(255)`` column so that all sync-route handlers
can filter on tenancy. The type matches ``Connector.org_id`` (set by
migration ``003_org_id_string``): the Zitadel resourceowner string.

No backfill. Historical rows that pre-date this migration keep
``org_id IS NULL``. Per-org filters (``WHERE org_id = '<x>'``) do not
match NULL rows, so pre-deploy sync history is invisible to any tenant
after this migration. Acceptable because:

  (a) sync_runs is operational/audit data — no business state is lost;
  (b) ``trigger_sync`` always populates org_id for new rows (handler
      requires the ``X-Org-ID`` header — see REQ-7.4 + REQ-8.1);
  (c) avoiding a backfill removes the cross-DB / orphan-cleanup
      complexity that v0.4.0 / v0.5.0 carried, with no functional cost.

The column stays nullable for historical rows. A future SPEC may flip
it to ``NOT NULL`` once those rows have aged out (sync_runs are
regularly truncated by retention policy) or are deliberately deleted.

Revision ID: 006_add_org_id_to_sync_runs
Revises: 005_add_sync_run_quality_status
Create Date: 2026-04-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_add_org_id_to_sync_runs"
down_revision: str | None = "005_add_sync_run_quality_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sync_runs",
        sa.Column("org_id", sa.String(255), nullable=True),
        schema="connector",
    )
    op.create_index(
        "ix_sync_runs_org_id",
        "sync_runs",
        ["org_id"],
        schema="connector",
    )


def downgrade() -> None:
    op.drop_index("ix_sync_runs_org_id", table_name="sync_runs", schema="connector")
    op.drop_column("sync_runs", "org_id", schema="connector")
