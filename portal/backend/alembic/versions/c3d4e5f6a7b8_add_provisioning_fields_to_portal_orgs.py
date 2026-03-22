"""add provisioning fields to portal_orgs

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-07 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('portal_orgs', sa.Column('slug', sa.String(64), nullable=False, server_default=''))
    op.add_column('portal_orgs', sa.Column('librechat_container', sa.String(128), nullable=True))
    op.add_column('portal_orgs', sa.Column('zitadel_librechat_client_id', sa.String(128), nullable=True))
    op.add_column('portal_orgs', sa.Column('zitadel_librechat_client_secret', sa.Text(), nullable=True))
    op.add_column('portal_orgs', sa.Column('provisioning_status', sa.String(32), nullable=False, server_default='pending'))
    op.create_index('ix_portal_orgs_slug', 'portal_orgs', ['slug'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_portal_orgs_slug', table_name='portal_orgs')
    op.drop_column('portal_orgs', 'provisioning_status')
    op.drop_column('portal_orgs', 'zitadel_librechat_client_secret')
    op.drop_column('portal_orgs', 'zitadel_librechat_client_id')
    op.drop_column('portal_orgs', 'librechat_container')
    op.drop_column('portal_orgs', 'slug')
