"""add system group fields

Revision ID: y1z2a3b4c5d6
Revises: x0y1z2a3b4c5
Create Date: 2026-03-24
"""

from alembic import op
import sqlalchemy as sa

revision = "y1z2a3b4c5d6"
down_revision = "x0y1z2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("portal_groups", sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("portal_groups", sa.Column("system_key", sa.String(32), nullable=True))
    # Unique: each org can only have one group with a given system_key
    op.create_index(
        "uq_portal_groups_org_system_key",
        "portal_groups",
        ["org_id", "system_key"],
        unique=True,
        postgresql_where=sa.text("system_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_portal_groups_org_system_key", table_name="portal_groups")
    op.drop_column("portal_groups", "system_key")
    op.drop_column("portal_groups", "is_system")
