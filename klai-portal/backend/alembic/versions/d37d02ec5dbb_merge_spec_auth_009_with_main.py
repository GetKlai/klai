"""merge SPEC-AUTH-009 with main

Revision ID: d37d02ec5dbb
Revises: 13bb3bb00d53, a1b2c3d4e5f6
Create Date: 2026-04-30 21:32:20.109934

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "d37d02ec5dbb"
down_revision: Union[str, Sequence[str], None] = ("13bb3bb00d53", "a1b2c3d4e5f6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
