"""add name column to transcriptions

Revision ID: 0003_add_name_to_transcriptions
Revises: 0002_fix_id_varchar_length
Create Date: 2026-03-12 00:00:00.000000

Optional user-provided name for a transcription (e.g. "Verkoopgesprek Jan").
Nullable so existing rows remain valid.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_name_to_transcriptions"
down_revision: Union[str, Sequence[str], None] = "0002_fix_id_varchar_length"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("name", sa.VARCHAR(255), nullable=True),
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_column("transcriptions", "name", schema="scribe")
