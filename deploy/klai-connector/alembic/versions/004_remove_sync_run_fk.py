"""Remove foreign key from sync_runs.connector_id to connector.connectors.

klai-connector is now a stateless execution plane. connector_id in sync_runs
is a portal connector UUID (portal_connectors.id in the portal DB), not a
local connector. The FK would break once portal-triggered syncs arrive.

Revision ID: 004_remove_sync_run_fk
Revises: 003_org_id_string
Create Date: 2026-03-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "004_remove_sync_run_fk"
down_revision: str | None = "003_org_id_string"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the FK constraint; connector_id remains as an indexed UUID column.
    op.drop_constraint(
        "sync_runs_connector_id_fkey",
        "sync_runs",
        schema="connector",
        type_="foreignkey",
    )


def downgrade() -> None:
    # Restore the FK (only valid if connector.connectors rows still exist).
    op.create_foreign_key(
        "sync_runs_connector_id_fkey",
        "sync_runs",
        "connectors",
        ["connector_id"],
        ["id"],
        source_schema="connector",
        referent_schema="connector",
        ondelete="CASCADE",
    )
