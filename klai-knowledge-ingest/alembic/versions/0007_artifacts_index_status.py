"""Add index_status column to knowledge.artifacts

Revision ID: f3a8e1b2d9c4
Revises: c1d4e7f2a8b6
Create Date: 2026-05-08 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a8e1b2d9c4"
down_revision: str = "c1d4e7f2a8b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Sentinel value for active artifacts (matches pg_store._SENTINEL)
_SENTINEL = 253402300800


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.artifacts
        ADD COLUMN IF NOT EXISTS index_status VARCHAR(20) NOT NULL DEFAULT 'synced'
        """
    )
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_artifacts_org_kb_index_status
        ON knowledge.artifacts (org_id, kb_slug, index_status)
        WHERE belief_time_end = {_SENTINEL}
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS knowledge.idx_artifacts_org_kb_index_status")
    op.execute("ALTER TABLE knowledge.artifacts DROP COLUMN IF EXISTS index_status")
