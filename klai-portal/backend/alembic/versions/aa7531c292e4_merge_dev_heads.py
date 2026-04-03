"""merge dev heads

Revision ID: aa7531c292e4
Revises: 1b8736eb6455, 27c92d265c51
Create Date: 2026-04-03 19:20:22.441341

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "aa7531c292e4"
down_revision: Union[str, Sequence[str], None] = ("1b8736eb6455", "27c92d265c51")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
