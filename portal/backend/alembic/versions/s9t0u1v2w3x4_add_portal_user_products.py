"""add portal_user_products table

Revision ID: s9t0u1v2w3x4
Revises: p6q7r8s9t0u1
Create Date: 2026-03-24

"""

import sqlalchemy as sa
from alembic import op

revision = "s9t0u1v2w3x4"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_user_products",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("zitadel_user_id", sa.String(64), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("product", sa.String(32), nullable=False),
        sa.Column(
            "enabled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("enabled_by", sa.String(64), nullable=False),
        sa.UniqueConstraint("zitadel_user_id", "product", name="uq_user_product"),
    )
    op.create_index(
        "ix_portal_user_products_org_product",
        "portal_user_products",
        ["org_id", "product"],
    )

    # Backfill: assign products based on each org's plan
    op.execute("""
        INSERT INTO portal_user_products (zitadel_user_id, org_id, product, enabled_by)
        SELECT pu.zitadel_user_id, po.id, unnest(
            CASE po.plan
                WHEN 'core' THEN ARRAY['chat']
                WHEN 'professional' THEN ARRAY['chat', 'scribe']
                WHEN 'complete' THEN ARRAY['chat', 'scribe', 'knowledge']
                ELSE ARRAY[]::text[]
            END
        ) AS product, 'migration' AS enabled_by
        FROM portal_users pu
        JOIN portal_orgs po ON pu.org_id = po.id
        ON CONFLICT (zitadel_user_id, product) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_portal_user_products_org_product", table_name="portal_user_products")
    op.drop_table("portal_user_products")
