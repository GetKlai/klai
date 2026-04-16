"""add portal_org_allowed_domains table

Revision ID: a1b2c3d4e5f6
Revises: z3a4b5c6d7e8
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_org_allowed_domains",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "domain", name="uq_org_allowed_domains_org_domain"),
        sa.UniqueConstraint("domain", name="uq_org_allowed_domains_domain_global"),
    )
    op.create_index("ix_portal_org_allowed_domains_domain", "portal_org_allowed_domains", ["domain"])


def downgrade() -> None:
    op.drop_index("ix_portal_org_allowed_domains_domain", table_name="portal_org_allowed_domains")
    op.drop_table("portal_org_allowed_domains")
