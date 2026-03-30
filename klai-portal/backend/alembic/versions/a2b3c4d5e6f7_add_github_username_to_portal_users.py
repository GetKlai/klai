"""add github_username to portal_users

Revision ID: a2b3c4d5e6f7
Revises: f8a9b0c1d2e3
Create Date: 2026-03-27

ISO 27001:2022 A.6.5 — staff termination
SPEC-SEC-002 REQ-07: GitHub org member removal during offboarding.
"""

from alembic import op
import sqlalchemy as sa


revision = "a2b3c4d5e6f7"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("github_username", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("portal_users", "github_username")
