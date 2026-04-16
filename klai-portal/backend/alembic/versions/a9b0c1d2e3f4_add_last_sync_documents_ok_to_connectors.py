"""add last_sync_documents_ok to portal_connectors

Revision ID: a9b0c1d2e3f4
Revises: 70d870b1f097
Create Date: 2026-04-16

"""

from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "70d870b1f097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_connectors",
        sa.Column("last_sync_documents_ok", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_connectors", "last_sync_documents_ok")
