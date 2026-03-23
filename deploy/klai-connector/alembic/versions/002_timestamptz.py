"""Fix timestamp columns to use TIMESTAMPTZ (timezone-aware).

Revision ID: 002_timestamptz
Revises: 001_initial
Create Date: 2026-03-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002_timestamptz"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "connectors",
        "last_sync_at",
        type_=sa.TIMESTAMP(timezone=True),
        schema="connector",
    )
    op.alter_column(
        "connectors",
        "created_at",
        type_=sa.TIMESTAMP(timezone=True),
        schema="connector",
    )
    op.alter_column(
        "connectors",
        "updated_at",
        type_=sa.TIMESTAMP(timezone=True),
        schema="connector",
    )
    op.alter_column(
        "sync_runs",
        "started_at",
        type_=sa.TIMESTAMP(timezone=True),
        schema="connector",
    )
    op.alter_column(
        "sync_runs",
        "completed_at",
        type_=sa.TIMESTAMP(timezone=True),
        schema="connector",
    )


def downgrade() -> None:
    op.alter_column(
        "connectors",
        "last_sync_at",
        type_=sa.TIMESTAMP(timezone=False),
        schema="connector",
    )
    op.alter_column(
        "connectors",
        "created_at",
        type_=sa.TIMESTAMP(timezone=False),
        schema="connector",
    )
    op.alter_column(
        "connectors",
        "updated_at",
        type_=sa.TIMESTAMP(timezone=False),
        schema="connector",
    )
    op.alter_column(
        "sync_runs",
        "started_at",
        type_=sa.TIMESTAMP(timezone=False),
        schema="connector",
    )
    op.alter_column(
        "sync_runs",
        "completed_at",
        type_=sa.TIMESTAMP(timezone=False),
        schema="connector",
    )
