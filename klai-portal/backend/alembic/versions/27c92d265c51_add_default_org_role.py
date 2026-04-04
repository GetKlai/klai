"""Add default_org_role to portal_knowledge_bases.

Revision ID: 27c92d265c51
Revises: 7a23f23c419d
Create Date: 2026-04-03
"""

from alembic import op
import sqlalchemy as sa

revision = "27c92d265c51"
down_revision = "7a23f23c419d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("default_org_role", sa.Text(), nullable=True, server_default=sa.text("'viewer'")),
    )


def downgrade() -> None:
    op.drop_column("portal_knowledge_bases", "default_org_role")
