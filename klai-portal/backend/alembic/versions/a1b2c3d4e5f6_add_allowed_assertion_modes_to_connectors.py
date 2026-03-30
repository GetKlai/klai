"""add allowed_assertion_modes to portal_connectors

Revision ID: a1b2c3d4e5f6
Revises: z2a3b4c5d6e7
Create Date: 2026-03-30

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a1b2c3d4e5f6"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_connectors",
        sa.Column("allowed_assertion_modes", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_connectors", "allowed_assertion_modes")
