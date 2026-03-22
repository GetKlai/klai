"""add plan and billing_cycle to portal_orgs

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-07 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("portal_orgs", sa.Column("plan", sa.Text(), nullable=False, server_default="professional"))
    op.add_column("portal_orgs", sa.Column("billing_cycle", sa.Text(), nullable=False, server_default="monthly"))


def downgrade() -> None:
    op.drop_column("portal_orgs", "billing_cycle")
    op.drop_column("portal_orgs", "plan")
