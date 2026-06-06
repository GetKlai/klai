"""product updates operator default

Revision ID: pu20260606c3
Revises: pu20260606b2
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "pu20260606c3"
down_revision: Union[str, Sequence[str], None] = "pu20260606b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE product_updates ALTER COLUMN published_via SET DEFAULT 'operator_script'")


def downgrade() -> None:
    op.execute("ALTER TABLE product_updates ALTER COLUMN published_via SET DEFAULT 'admin_api'")
