"""add moneybird fields to portal_orgs

Revision ID: a1b2c3d4e5f6
Revises: d64fdcfecf32
Create Date: 2026-03-07 15:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d64fdcfecf32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("portal_orgs", sa.Column("moneybird_contact_id", sa.Text(), nullable=True))
    op.add_column("portal_orgs", sa.Column("billing_status", sa.Text(), nullable=False, server_default="pending"))


def downgrade() -> None:
    op.drop_column("portal_orgs", "billing_status")
    op.drop_column("portal_orgs", "moneybird_contact_id")
