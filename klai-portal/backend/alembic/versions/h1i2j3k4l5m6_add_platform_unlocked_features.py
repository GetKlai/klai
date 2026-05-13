"""Add platform_unlocked_features column to portal_orgs.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 5A.

Revision ID: h1i2j3k4l5m6
Revises: g5h6i7j8k9l0
Create Date: 2026-05-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "h1i2j3k4l5m6"
down_revision = "g5h6i7j8k9l0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column(
            "platform_unlocked_features",
            sa.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "platform_unlocked_features")
