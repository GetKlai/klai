"""add litellm_team_key to portal_orgs

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-03-21

"""
from alembic import op
import sqlalchemy as sa

revision = 'l2m3n4o5p6q7'
down_revision = 'k1l2m3n4o5p6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'portal_orgs',
        sa.Column('litellm_team_key', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('portal_orgs', 'litellm_team_key')
