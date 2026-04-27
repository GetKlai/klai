"""add error_reason to transcriptions for stranded-row reaper

Revision ID: 0007_c5f9e3a4
Revises: 0006_b4e8d2f3
Create Date: 2026-04-25 12:00:00.000000

SPEC-SEC-HYGIENE-001 REQ-35. The stranded-row reaper at worker startup
flips `status='processing'` rows older than the timeout to `failed` with
`error_reason='worker_restart_stranded'`. Existing legitimate transitions
to `failed` (whisper error during transcribe) leave `error_reason` NULL.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_c5f9e3a4"
down_revision: str | Sequence[str] | None = "0006_b4e8d2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("error_reason", sa.VARCHAR(64), nullable=True),
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_column("transcriptions", "error_reason", schema="scribe")
