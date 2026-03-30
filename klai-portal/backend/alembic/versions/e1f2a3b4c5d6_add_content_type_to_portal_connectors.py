"""add content_type to portal_connectors

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_connectors",
        sa.Column("content_type", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_connectors", "content_type")
