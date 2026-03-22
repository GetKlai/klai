"""add role to portal_users

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-10

"""

from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("role", sa.String(20), nullable=False, server_default="admin"),
    )
    op.create_check_constraint("ck_portal_users_role", "portal_users", "role IN ('admin', 'member')")


def downgrade() -> None:
    op.drop_constraint("ck_portal_users_role", "portal_users", type_="check")
    op.drop_column("portal_users", "role")
