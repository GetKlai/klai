"""Initial migration: connector schema, connectors and sync_runs tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create connector schema and tables."""
    # Create schema
    op.execute("CREATE SCHEMA IF NOT EXISTS connector")

    # Create connectors table
    op.create_table(
        "connectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("connector_type", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False),
        sa.Column("credentials_enc", sa.LargeBinary, nullable=True),
        sa.Column("encryption_key_version", sa.Integer, server_default="1"),
        sa.Column("schedule", sa.String(100), nullable=True),
        sa.Column("is_enabled", sa.Boolean, server_default="true", nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        schema="connector",
    )

    op.create_index("idx_connectors_org_id", "connectors", ["org_id"], schema="connector")

    # Create sync_runs table
    op.create_table(
        "sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "connector_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("connector.connectors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("documents_total", sa.Integer, server_default="0"),
        sa.Column("documents_ok", sa.Integer, server_default="0"),
        sa.Column("documents_failed", sa.Integer, server_default="0"),
        sa.Column("bytes_processed", sa.BigInteger, server_default="0"),
        sa.Column("error_details", postgresql.JSONB, nullable=True),
        sa.Column("cursor_state", postgresql.JSONB, nullable=True),
        schema="connector",
    )

    op.create_index(
        "idx_sync_runs_connector",
        "sync_runs",
        ["connector_id", sa.text("started_at DESC")],
        schema="connector",
    )


def downgrade() -> None:
    """Drop tables and schema."""
    op.drop_table("sync_runs", schema="connector")
    op.drop_table("connectors", schema="connector")
    op.execute("DROP SCHEMA IF EXISTS connector CASCADE")
