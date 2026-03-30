"""add status column to portal_users

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-03-24

"""

import sqlalchemy as sa
from alembic import op

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_portal_users_status",
        "portal_users",
        "status IN ('active', 'suspended', 'offboarded')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_portal_users_status", "portal_users", type_="check")
    op.drop_column("portal_users", "status")
