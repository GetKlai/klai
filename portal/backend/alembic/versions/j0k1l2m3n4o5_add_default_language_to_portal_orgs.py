"""add default_language to portal_orgs

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column("default_language", sa.String(8), nullable=False, server_default="nl"),
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "default_language")
