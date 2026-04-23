"""Drop legacy connector.connectors table.

SPEC-CONNECTOR-CLEANUP-001 REQ-01: the legacy connector registry table
is removed. The table was empty at all tenants in production. Connector
configuration is owned by ``public.portal_connectors`` in the portal
database; klai-connector fetches it at sync time via the portal API.

The FK from ``connector.sync_runs.connector_id`` to
``connector.connectors.id`` was already dropped in
``004_remove_sync_run_fk``. SPEC-CONNECTOR-CLEANUP-001 Fase 5 adds a new
FK to ``public.portal_connectors.id``.

Downgrade restores the post-003 schema (``org_id`` as ``String(255)``,
not ``UUID``) without data. Restoration of historical row data is out of
scope; the table was always empty in production.

Revision ID: 006_drop_connectors_table
Revises: 005_add_sync_run_quality_status
Create Date: 2026-04-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "006_drop_connectors_table"
down_revision: str | None = "005_add_sync_run_quality_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("idx_connectors_org_id", table_name="connectors", schema="connector")
    op.drop_table("connectors", schema="connector")


def downgrade() -> None:
    op.create_table(
        "connectors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("org_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connector_type", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("credentials_enc", sa.LargeBinary, nullable=True),
        sa.Column("encryption_key_version", sa.Integer, server_default="1"),
        sa.Column("schedule", sa.String(100), nullable=True),
        sa.Column("is_enabled", sa.Boolean, server_default="true", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="connector",
    )
    op.create_index(
        "idx_connectors_org_id",
        "connectors",
        ["org_id"],
        schema="connector",
    )
