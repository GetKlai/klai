"""add summary_json column to transcriptions

Revision ID: 0004_add_summary_to_transcriptions
Revises: 0003_add_name_to_transcriptions
Create Date: 2026-03-24 00:00:00.000000

Adds nullable JSONB column for AI-generated summaries.
NULL means the transcription has never been summarized.
summary_json.type stores the recording type used for summarization.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_add_summary_to_transcriptions"
down_revision: Union[str, Sequence[str], None] = "0003_add_name_to_transcriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_column("transcriptions", "summary_json", schema="scribe")
