"""add product_events table

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-03-24

"""

from alembic import op
import sqlalchemy as sa

revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_product_events_type_created",
        "product_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        "idx_product_events_org_created",
        "product_events",
        ["org_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_product_events_org_created", table_name="product_events")
    op.drop_index("idx_product_events_type_created", table_name="product_events")
    op.drop_table("product_events")
