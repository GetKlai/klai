"""fix role server_default to member

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-03-11

The previous migration set server_default='admin', meaning any INSERT without
an explicit role would silently create an admin. The safe default is 'member';
the signup endpoint now sets role='admin' explicitly for org creators.
"""
from alembic import op


revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "portal_users",
        "role",
        server_default="member",
    )


def downgrade() -> None:
    op.alter_column(
        "portal_users",
        "role",
        server_default="admin",
    )
