"""Add display_name and email to portal_users.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portal_users", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("portal_users", sa.Column("email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("portal_users", "email")
    op.drop_column("portal_users", "display_name")
