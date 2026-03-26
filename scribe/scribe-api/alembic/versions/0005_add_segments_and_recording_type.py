"""add recording_type and segments_json columns to transcriptions

Revision ID: 0005_a3f7c1e2
Revises: 0004_add_summary_to_transcriptions
Create Date: 2026-03-26 00:00:00.000000

Adds nullable recording_type (VARCHAR(32)) and segments_json (JSON)
columns to scribe.transcriptions.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_a3f7c1e2"
down_revision: Union[str, Sequence[str], None] = "0004_add_summary_to_transcriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("recording_type", sa.VARCHAR(32), nullable=True),
        schema="scribe",
    )
    op.add_column(
        "transcriptions",
        sa.Column("segments_json", sa.JSON(), nullable=True),
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_column("transcriptions", "segments_json", schema="scribe")
    op.drop_column("transcriptions", "recording_type", schema="scribe")
