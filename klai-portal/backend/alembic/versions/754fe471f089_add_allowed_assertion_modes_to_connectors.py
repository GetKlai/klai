"""add allowed_assertion_modes to portal_connectors

Revision ID: 754fe471f089
Revises: e1f2a3b4c5d6
Create Date: 2026-03-30

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "754fe471f089"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_connectors",
        sa.Column("allowed_assertion_modes", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_connectors", "allowed_assertion_modes")
