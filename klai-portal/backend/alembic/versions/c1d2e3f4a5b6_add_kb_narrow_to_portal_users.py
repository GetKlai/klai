"""add kb_narrow to portal_users

Revision ID: c1d2e3f4a5b6
Revises: 754fe471f089
Create Date: 2026-03-31

Adds kb_narrow column: when true, the LiteLLM knowledge hook instructs the
model to answer strictly from KB chunks only (no general knowledge fallback).
Defaults to false (broad mode — KB as additional context).
"""

from alembic import op
import sqlalchemy as sa

revision = "c1d2e3f4a5b6"
down_revision = "754fe471f089"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("kb_narrow", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("portal_users", "kb_narrow")
