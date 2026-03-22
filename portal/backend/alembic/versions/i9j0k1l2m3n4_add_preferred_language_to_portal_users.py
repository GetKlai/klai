"""add preferred_language to portal_users

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-03-11

"""

from alembic import op
import sqlalchemy as sa

revision = "i9j0k1l2m3n4"
down_revision = "h8i9j0k1l2m3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("preferred_language", sa.String(8), nullable=False, server_default="nl"),
    )


def downgrade() -> None:
    op.drop_column("portal_users", "preferred_language")
