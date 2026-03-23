"""fix id column length from VARCHAR(32) to VARCHAR(64)

Revision ID: 0002_fix_id_varchar_length
Revises: 0001_create_scribe
Create Date: 2026-03-12 00:00:00.000000

txn_id is generated as "txn_" + uuid4().hex = 4 + 32 = 36 chars.
The original migration defined id as VARCHAR(32), which is too short.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_fix_id_varchar_length"
down_revision: Union[str, Sequence[str], None] = "0001_create_scribe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "transcriptions",
        "id",
        existing_type=sa.VARCHAR(32),
        type_=sa.VARCHAR(64),
        schema="scribe",
    )


def downgrade() -> None:
    op.alter_column(
        "transcriptions",
        "id",
        existing_type=sa.VARCHAR(64),
        type_=sa.VARCHAR(32),
        schema="scribe",
    )
