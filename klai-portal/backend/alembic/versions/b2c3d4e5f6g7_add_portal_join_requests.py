"""add portal_join_requests table

Revision ID: b2c3d4e5f6g7
Revises: 23c5c8b48669
Create Date: 2026-04-16

2026-04-22: down_revision updated from the legacy hand-typed
`a1b2c3d4e5f6` to the renamed `23c5c8b48669`
(add_portal_org_allowed_domains). No functional change to the chain.
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6g7"
down_revision = "23c5c8b48669"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_join_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zitadel_user_id", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(64), nullable=True),
        sa.Column("approval_token", sa.String(128), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_portal_join_requests_org_status", "portal_join_requests", ["org_id", "status"])
    op.create_index("ix_portal_join_requests_zitadel_user_id", "portal_join_requests", ["zitadel_user_id"])


def downgrade() -> None:
    op.drop_index("ix_portal_join_requests_zitadel_user_id", table_name="portal_join_requests")
    op.drop_index("ix_portal_join_requests_org_status", table_name="portal_join_requests")
    op.drop_table("portal_join_requests")
