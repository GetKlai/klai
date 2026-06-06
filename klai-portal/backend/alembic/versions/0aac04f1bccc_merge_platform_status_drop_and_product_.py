"""merge platform-status-drop and product-updates heads

Revision ID: 0aac04f1bccc
Revises: c4d7e9f1a2b6, pu20260606c3
Create Date: 2026-06-06 17:56:30.971795

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0aac04f1bccc"
down_revision: Union[str, Sequence[str], None] = ("c4d7e9f1a2b6", "pu20260606c3")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
