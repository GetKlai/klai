"""Slim portal_users: remove identity fields duplicated from Zitadel

User details (email, first_name, last_name) are now fetched live from Zitadel.
portal_users becomes a pure mapping table: zitadel_user_id -> org_id.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("portal_users", "email")
    op.drop_column("portal_users", "first_name")
    op.drop_column("portal_users", "last_name")


def downgrade() -> None:
    op.add_column("portal_users", sa.Column("last_name", sa.String(100), nullable=False, server_default=""))
    op.add_column("portal_users", sa.Column("first_name", sa.String(100), nullable=False, server_default=""))
    op.add_column("portal_users", sa.Column("email", sa.String(255), nullable=False, server_default=""))
