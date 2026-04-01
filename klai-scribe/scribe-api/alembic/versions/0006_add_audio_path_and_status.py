"""add audio_path and status columns, relax NOT NULL on transcription fields

Revision ID: 0006_b4e8d2f3
Revises: 0005_a3f7c1e2
Create Date: 2026-04-01 12:00:00.000000

Audio is now persisted to disk before transcription. When whisper fails,
the record stays with status='failed' and the audio file is retained for
retry. Columns that are only populated after successful transcription
(text, language, duration, etc.) become nullable.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_b4e8d2f3"
down_revision: Union[str, Sequence[str], None] = "0005_a3f7c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("status", sa.VARCHAR(16), nullable=False, server_default="transcribed"),
        schema="scribe",
    )
    op.add_column(
        "transcriptions",
        sa.Column("audio_path", sa.VARCHAR(512), nullable=True),
        schema="scribe",
    )

    # Relax NOT NULL on columns that are empty for failed transcriptions
    op.alter_column("transcriptions", "text", nullable=True, schema="scribe")
    op.alter_column("transcriptions", "language", nullable=True, schema="scribe")
    op.alter_column("transcriptions", "duration_seconds", nullable=True, schema="scribe")
    op.alter_column("transcriptions", "inference_time_seconds", nullable=True, schema="scribe")
    op.alter_column("transcriptions", "provider", nullable=True, schema="scribe")
    op.alter_column("transcriptions", "model", nullable=True, schema="scribe")


def downgrade() -> None:
    # Delete failed records that have NULL text before restoring NOT NULL
    op.execute("DELETE FROM scribe.transcriptions WHERE text IS NULL")

    op.alter_column("transcriptions", "model", nullable=False, schema="scribe")
    op.alter_column("transcriptions", "provider", nullable=False, schema="scribe")
    op.alter_column("transcriptions", "inference_time_seconds", nullable=False, schema="scribe")
    op.alter_column("transcriptions", "duration_seconds", nullable=False, schema="scribe")
    op.alter_column("transcriptions", "language", nullable=False, schema="scribe")
    op.alter_column("transcriptions", "text", nullable=False, schema="scribe")

    op.drop_column("transcriptions", "audio_path", schema="scribe")
    op.drop_column("transcriptions", "status", schema="scribe")
