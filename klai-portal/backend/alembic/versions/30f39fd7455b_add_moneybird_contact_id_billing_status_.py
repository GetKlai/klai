"""add_moneybird_contact_id_billing_status_to_portal_orgs

Revision ID: 30f39fd7455b
Revises: a8f03dfa5d09
Create Date: 2026-04-09 09:58:54.059267

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '30f39fd7455b'
down_revision: Union[str, Sequence[str], None] = 'a8f03dfa5d09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('portal_orgs', sa.Column('moneybird_contact_id', sa.Text(), nullable=True))
    op.add_column('portal_orgs', sa.Column('billing_status', sa.Text(), nullable=False, server_default='pending'))


def downgrade() -> None:
    op.drop_column('portal_orgs', 'billing_status')
    op.drop_column('portal_orgs', 'moneybird_contact_id')
