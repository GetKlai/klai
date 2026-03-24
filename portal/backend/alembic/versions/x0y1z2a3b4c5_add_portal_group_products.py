"""add portal_group_products table

Revision ID: x0y1z2a3b4c5
Revises: w3x4y5z6a7b8
Create Date: 2026-03-24

"""

import sqlalchemy as sa
from alembic import op

revision = "x0y1z2a3b4c5"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_group_products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("portal_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("product", sa.String(32), nullable=False),
        sa.Column(
            "enabled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("enabled_by", sa.String(64), nullable=False),
        sa.UniqueConstraint("group_id", "product", name="uq_group_products_group_product"),
    )
    op.create_index("ix_group_products_group_id", "portal_group_products", ["group_id"])
    op.create_index("ix_group_products_org_product", "portal_group_products", ["org_id", "product"])


def downgrade() -> None:
    op.drop_index("ix_group_products_org_product", table_name="portal_group_products")
    op.drop_index("ix_group_products_group_id", table_name="portal_group_products")
    op.drop_table("portal_group_products")
