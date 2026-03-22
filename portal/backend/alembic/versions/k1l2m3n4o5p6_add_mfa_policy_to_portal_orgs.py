"""add mfa_policy to portal_orgs

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-03-11

"""

from alembic import op
import sqlalchemy as sa

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column("mfa_policy", sa.String(16), nullable=False, server_default="optional"),
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "mfa_policy")
