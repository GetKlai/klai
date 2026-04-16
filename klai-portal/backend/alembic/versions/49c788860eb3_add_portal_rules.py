"""add portal_rules table

Revision ID: 49c788860eb3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-16

Adds the portal_rules table for per-tenant policy/guardrail rules.
Mirrors portal_templates structure with rule_text instead of prompt_text.
"""

import sqlalchemy as sa
from alembic import op

revision = "49c788860eb3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rule_text", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_portal_rule_org_slug", "portal_rules", ["org_id", "slug"]
    )
    op.create_index(
        "ix_portal_rule_org_id", "portal_rules", ["org_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_portal_rule_org_id", table_name="portal_rules")
    op.drop_constraint("uq_portal_rule_org_slug", "portal_rules")
    op.drop_table("portal_rules")
