"""Add org_id column to connector.sync_runs (tenant scoping).

SPEC-SEC-TENANT-001 REQ-7.1 (v0.5.0): the ``connector.sync_runs`` table
gains an ``org_id VARCHAR(255) NOT NULL`` column so that all sync-route
handlers can filter on tenancy. The type matches ``Connector.org_id``
(set by migration ``003_org_id_string``): the Zitadel resourceowner
string.

Backfill is intra-DB: every existing ``sync_runs`` row joins against
its parent ``connector.connectors`` row (via ``connector_id``) and copies
the org_id forward in a single SQL statement. No cross-DB script is
required — the v0.4.0 SPEC HISTORY documents the simplification.

Migration shape (single transaction):
  1. Add ``org_id`` as nullable.
  2. Backfill via ``UPDATE … FROM connector.connectors`` join.
  3. ``ALTER … SET NOT NULL`` (commit point — fails loud if any row
     lacks a parent connector).
  4. Add index ``ix_sync_runs_org_id`` for the per-org filter on
     ``trigger_sync`` / ``list_sync_runs`` / ``get_sync_run``.

Orphans: a ``sync_runs`` row whose parent connector has been deleted
from ``connector.connectors`` keeps ``org_id IS NULL`` after step 2 and
trips the NOT NULL alter in step 3. Per the SPEC's Risks table the
runbook deletes those rows BEFORE applying the migration:
  ``DELETE FROM connector.sync_runs WHERE connector_id NOT IN
   (SELECT id FROM connector.connectors)``.
This is consistent with the SPEC-CONNECTOR-CLEANUP-001 REQ-04 follow-up
(connector-delete cascade for sync_runs).

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
    # 1. Add nullable column so existing rows do not break the constraint.
    op.add_column(
        "sync_runs",
        sa.Column("org_id", sa.String(255), nullable=True),
        schema="connector",
    )

    # 2. Backfill from the sibling connector.connectors table. Intra-DB join;
    #    no cross-DB script needed (v0.5.0 simplification — see SPEC HISTORY).
    op.execute(
        """
        UPDATE connector.sync_runs r
        SET org_id = c.org_id
        FROM connector.connectors c
        WHERE r.connector_id = c.id
        """
    )

    # 3. Enforce NOT NULL. Any orphan sync_run (parent connector deleted upstream)
    #    trips this; the runbook pre-step deletes orphans BEFORE the migration
    #    runs. SPEC-SEC-TENANT-001 REQ-7.1 + Risks table.
    op.alter_column(
        "sync_runs",
        "org_id",
        nullable=False,
        schema="connector",
    )

    # 4. Index for per-org filtering on the three sync-route handlers
    #    (trigger_sync / list_sync_runs / get_sync_run).
    op.create_index(
        "ix_sync_runs_org_id",
        "sync_runs",
        ["org_id"],
        schema="connector",
    )


def downgrade() -> None:
    op.drop_index("ix_sync_runs_org_id", table_name="sync_runs", schema="connector")
    op.drop_column("sync_runs", "org_id", schema="connector")
