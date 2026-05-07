"""merge SPEC-MCP-AUTH-001 with main

Revision ID: e84a10348987
Revises: 2f7d1eae1198, 9f4e2c8a1b7d
Create Date: 2026-05-07 08:07:57.980766

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "e84a10348987"
down_revision: Union[str, Sequence[str], None] = ("2f7d1eae1198", "9f4e2c8a1b7d")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
