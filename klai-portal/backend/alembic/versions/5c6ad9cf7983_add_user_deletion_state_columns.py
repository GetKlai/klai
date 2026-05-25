"""Add portal_users.deletion_status, failure_reason, last_attempted_step.

REQ-4 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): State tracking for the
platform user-delete state machine. Allows partial failures to be recorded
and retried via POST /api/admin/platform/users/{uid}/retry-delete.

Revision ID: 5c6ad9cf7983
Revises: 57b2c33efe55
Create Date: 2026-05-24

# @MX:NOTE: upgrade() adds three nullable columns with no defaults.
#   Existing rows will have NULL in all three (no deletion attempt yet).
#   The columns are populated by user_deletion_orchestrator._mark_user_delete_failed
#   when a step fails.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "5c6ad9cf7983"
down_revision: str | None = "57b2c33efe55"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Three nullable columns — existing rows keep NULL (no ongoing deletion).
    op.add_column(
        "portal_users",
        sa.Column("deletion_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "portal_users",
        sa.Column("failure_reason", JSONB, nullable=True),
    )
    op.add_column(
        "portal_users",
        sa.Column("last_attempted_step", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_users", "last_attempted_step")
    op.drop_column("portal_users", "failure_reason")
    op.drop_column("portal_users", "deletion_status")
