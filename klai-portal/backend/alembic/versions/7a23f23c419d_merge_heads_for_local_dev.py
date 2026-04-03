"""merge heads for local dev

Revision ID: 7a23f23c419d
Revises: 83a82cc61aee, c1d2e3f4a5b6
Create Date: 2026-04-03 09:33:39.242147

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a23f23c419d'
down_revision: Union[str, Sequence[str], None] = ('83a82cc61aee', 'c1d2e3f4a5b6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
