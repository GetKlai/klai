"""pending_links: portal link tokens awaiting their Slack OAuth callback.

Revision ID: 002_pending_links
Revises: 001_initial
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_pending_links"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pending_links",
        sa.Column("state", sa.String(200), primary_key=True),
        sa.Column("token_enc", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("pending_links")
