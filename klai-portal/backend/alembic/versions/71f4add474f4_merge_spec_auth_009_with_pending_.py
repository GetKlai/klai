"""merge SPEC-AUTH-009 with pending migrations

Revision ID: 71f4add474f4
Revises: a1b2c3d4e5f6, v2m3e4r5g6h7
Create Date: 2026-04-30 21:21:24.575762

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "71f4add474f4"
down_revision: Union[str, Sequence[str], None] = ("a1b2c3d4e5f6", "v2m3e4r5g6h7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
