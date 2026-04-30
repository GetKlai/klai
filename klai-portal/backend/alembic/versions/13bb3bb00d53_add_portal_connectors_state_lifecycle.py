"""add_portal_connectors_state_lifecycle

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-01.

Adds a transient lifecycle ``state`` column to ``portal_connectors``. Values:
    - 'active'   - connector is live and usable
    - 'deleting' - DELETE endpoint flipped state, async purge worker is
                   processing the cascade. Read-paths hide the row.

Hard-delete still removes the row entirely. The 'deleting' state is purely
a marker that the orchestrator-worker is responsible for the row, so the
DELETE endpoint can return 202 immediately.

Migration is additive only - no data deletion. Backfill sets every existing
row to 'active'. Single revision (no merge needed).

Revision ID: 13bb3bb00d53
Revises: v2m3e4r5g6h7
Create Date: 2026-04-30 14:40:33.428643

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "13bb3bb00d53"
down_revision: Union[str, Sequence[str], None] = "v2m3e4r5g6h7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Add nullable column with explicit default so existing rows backfill.
    op.add_column(
        "portal_connectors",
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=True,
            server_default=sa.text("'active'"),
        ),
    )
    # 2. Backfill: anything still NULL becomes 'active' (defensive - server_default
    #    should already cover this; the explicit UPDATE is for the rare case where
    #    rows were inserted between schema-add and column-default-application on
    #    older PostgreSQL releases).
    op.execute("UPDATE portal_connectors SET state = 'active' WHERE state IS NULL")
    # 3. Lock down: NOT NULL + CHECK constraint.
    op.alter_column("portal_connectors", "state", nullable=False)
    op.create_check_constraint(
        "ck_portal_connectors_state",
        "portal_connectors",
        "state IN ('active', 'deleting')",
    )
    # 4. Composite index for list-endpoint filter performance:
    #    ``WHERE kb_id = ? AND state = 'active'`` is the hot path.
    op.create_index(
        "ix_portal_connectors_state_kb",
        "portal_connectors",
        ["state", "kb_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_portal_connectors_state_kb", table_name="portal_connectors")
    op.drop_constraint(
        "ck_portal_connectors_state", "portal_connectors", type_="check"
    )
    op.drop_column("portal_connectors", "state")
