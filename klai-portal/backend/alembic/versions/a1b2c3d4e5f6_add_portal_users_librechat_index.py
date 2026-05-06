"""Add portal_users_librechat_index for B-6 cross-tenant pivot fix

Revision ID: a1b2c3d4e5f6
Revises: z3a4b5c6d7e8
Create Date: 2026-05-05

SPEC-TI-010C (B-6): Eliminates cross-tenant pivot via org_id query-param on
feature_knowledge endpoint. Adds a lookup table that maps LibreChat MongoDB
ObjectIds to their owning org, so the endpoint can resolve org from the
ObjectId without trusting a caller-supplied org_id.
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_users_librechat_index",
        sa.Column("librechat_object_id", sa.Text(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("zitadel_user_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["portal_orgs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("librechat_object_id"),
    )
    op.create_index(
        "ix_portal_users_librechat_index_zitadel",
        "portal_users_librechat_index",
        ["zitadel_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_portal_users_librechat_index_zitadel")
    op.drop_table("portal_users_librechat_index")
