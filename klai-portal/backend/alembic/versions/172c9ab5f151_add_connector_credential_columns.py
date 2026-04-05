"""add encrypted_credentials and connector_dek_enc columns

Revision ID: 172c9ab5f151
Revises: b6c7d8e9f0a1
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa

revision = "172c9ab5f151"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_connectors",
        sa.Column("encrypted_credentials", sa.LargeBinary(), nullable=True),
    )
    op.add_column(
        "portal_orgs",
        sa.Column("connector_dek_enc", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "connector_dek_enc")
    op.drop_column("portal_connectors", "encrypted_credentials")
