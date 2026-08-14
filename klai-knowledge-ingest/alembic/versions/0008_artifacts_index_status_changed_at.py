"""Add index_status_changed_at column to knowledge.artifacts

Tracks WHEN index_status last transitioned (epoch seconds). Consumed by the
portal sources list so the frontend "Bezig sinds Xm / Hangt al Xm" badge can
measure from the (re)sync start instead of the artifact creation date — a
re-synced 8-day-old artifact previously showed "Hangt al 11384m" immediately.

NULL means "never transitioned since this column landed"; readers fall back
to created_at.

Revision ID: b7c2d9e4f1a3
Revises: f3a8e1b2d9c4
Create Date: 2026-08-14 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c2d9e4f1a3"
down_revision: str = "f3a8e1b2d9c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.artifacts
        ADD COLUMN IF NOT EXISTS index_status_changed_at BIGINT
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.artifacts DROP COLUMN IF EXISTS index_status_changed_at")
