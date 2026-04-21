"""change portal_users unique constraint for multi-org support

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-04-16
"""

from alembic import op

revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old global unique constraint on zitadel_user_id
    op.drop_index("ix_portal_users_zitadel_user_id", table_name="portal_users")
    op.drop_constraint("portal_users_zitadel_user_id_key", table_name="portal_users", type_="unique")

    # Add composite unique constraint: one user per org
    op.create_unique_constraint(
        "uq_portal_users_zitadel_user_org",
        "portal_users",
        ["zitadel_user_id", "org_id"],
    )
    # Re-create index (non-unique now)
    op.create_index("ix_portal_users_zitadel_user_id", "portal_users", ["zitadel_user_id"])


def downgrade() -> None:
    op.drop_index("ix_portal_users_zitadel_user_id", table_name="portal_users")
    op.drop_constraint("uq_portal_users_zitadel_user_org", table_name="portal_users", type_="unique")
    op.create_index("ix_portal_users_zitadel_user_id", "portal_users", ["zitadel_user_id"], unique=True)
