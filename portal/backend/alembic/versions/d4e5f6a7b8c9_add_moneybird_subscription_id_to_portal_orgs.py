"""add moneybird_subscription_id to portal_orgs

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'portal_orgs',
        sa.Column('moneybird_subscription_id', sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('portal_orgs', 'moneybird_subscription_id')
