"""add portal_templates table

Revision ID: f7a8b9c0d1e2
Revises: b1f2a3c4d5e6, z3a4b5c6d7e8
Create Date: 2026-04-16

Merges two heads and adds the portal_templates table for prompt templates.
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "d4e5f6g7h8i9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("portal_orgs.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="global"),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_portal_template_org_slug", "portal_templates", ["org_id", "slug"]
    )
    op.create_index(
        "ix_portal_template_org_id", "portal_templates", ["org_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_portal_template_org_id", table_name="portal_templates")
    op.drop_constraint("uq_portal_template_org_slug", "portal_templates")
    op.drop_table("portal_templates")
