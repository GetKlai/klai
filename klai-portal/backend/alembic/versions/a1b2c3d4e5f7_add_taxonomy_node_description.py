"""add description column to portal_taxonomy_nodes

Revision ID: a1b2c3d4e5f7
Revises: 172c9ab5f151
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "172c9ab5f151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_taxonomy_nodes",
        sa.Column("description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_taxonomy_nodes", "description")
