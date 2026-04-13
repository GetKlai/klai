"""merge heads

Revision ID: 70d870b1f097
Revises: b1f2a3c4d5e6, z3a4b5c6d7e8
Create Date: 2026-04-13

"""

from typing import Sequence, Union

revision: str = "70d870b1f097"
down_revision: Union[str, Sequence[str], None] = ("b1f2a3c4d5e6", "z3a4b5c6d7e8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
