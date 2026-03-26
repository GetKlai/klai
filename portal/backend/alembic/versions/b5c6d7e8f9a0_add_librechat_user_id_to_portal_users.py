"""add librechat_user_id to portal_users

Revision ID: b5c6d7e8f9a0
Revises: z2a3b4c5d6e7
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "b5c6d7e8f9a0"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("librechat_user_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_portal_users_librechat_user_id",
        "portal_users",
        ["librechat_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_portal_users_librechat_user_id", table_name="portal_users")
    op.drop_column("portal_users", "librechat_user_id")
