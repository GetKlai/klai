"""make feedback triage suggestions idempotent per model

Revision ID: e8f9a0b1c2d4
Revises: d7e8f9a0b1c3
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op

revision: str = "e8f9a0b1c2d4"
down_revision: str | None = "d7e8f9a0b1c3"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_feedback_triage_suggestions_submission_model",
        "feedback_triage_suggestions",
        ["submission_id", "model"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_feedback_triage_suggestions_submission_model",
        table_name="feedback_triage_suggestions",
    )
