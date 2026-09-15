"""Widget answer-signals marker — per-answer certainty column on widget_messages.

The DDL itself runs as the klai superuser in
``post_deploy_b5d2f8a4c7e1_widget_messages_answer_signals.sql`` because
``widget_messages`` is klai-owned and FORCE-RLS (see
post_deploy_a4f72e913c8b_widget_conversations_rls.sql): portal_api — the
role alembic runs as — cannot ALTER TABLE it. Same marker pattern as
f4a8c2e6b1d9 / a1c4e7b2d9f3.

This file exists only so alembic can advance its head past a1c4e7b2d9f3.

Revision ID: b5d2f8a4c7e1
Revises: a1c4e7b2d9f3
Create Date: 2026-09-15
"""

from __future__ import annotations

revision = "b5d2f8a4c7e1"
down_revision = "a1c4e7b2d9f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling
    post_deploy SQL by the klai superuser.
    """
    # Intentionally empty: see post_deploy_b5d2f8a4c7e1_widget_messages_answer_signals.sql


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
    pass
