"""add portal_groups and portal_group_memberships tables

Revision ID: q7r8s9t0u1v2
Revises: s9t0u1v2w3x4
Create Date: 2026-03-24

"""

import sqlalchemy as sa
from alembic import op

revision = "q7r8s9t0u1v2"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["portal_orgs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portal_groups_org_id", "portal_groups", ["org_id"])
    # Case-insensitive unique constraint on (org_id, lower(name))
    op.execute(
        "CREATE UNIQUE INDEX uq_group_org_name_lower ON portal_groups (org_id, LOWER(name))"
    )

    op.create_table(
        "portal_group_memberships",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("zitadel_user_id", sa.String(64), nullable=False),
        sa.Column("is_group_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["portal_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "zitadel_user_id", name="uq_group_membership"),
    )


def downgrade() -> None:
    op.drop_table("portal_group_memberships")
    op.execute("DROP INDEX IF EXISTS uq_group_org_name_lower")
    op.drop_index("ix_portal_groups_org_id", table_name="portal_groups")
    op.drop_table("portal_groups")
