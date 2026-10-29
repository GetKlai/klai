"""add background-job columns to scribe transcriptions

Revision ID: 0009_c6f146c0
Revises: 0008_f4a2d9b1
Create Date: 2026-10-29 00:00:00.000000

Transcription now runs as a background job claimed from this table
(app.services.jobs). `attempts` caps how often a row is retried after a
crash, `claimed_at` marks a running job so a stale claim can be picked up
again, and `language_hint` keeps the caller's optional language for the job.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_c6f146c0"
down_revision: str | Sequence[str] | None = "0008_f4a2d9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("attempts", sa.INTEGER(), nullable=False, server_default="0"),
        schema="scribe",
    )
    op.add_column(
        "transcriptions",
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        schema="scribe",
    )
    op.add_column(
        "transcriptions",
        sa.Column("language_hint", sa.VARCHAR(16), nullable=True),
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_column("transcriptions", "language_hint", schema="scribe")
    op.drop_column("transcriptions", "claimed_at", schema="scribe")
    op.drop_column("transcriptions", "attempts", schema="scribe")
