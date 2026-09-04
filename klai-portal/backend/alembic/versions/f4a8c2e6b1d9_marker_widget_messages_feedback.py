"""Widget feedback marker — rating + turn_id columns on widget_messages.

The DDL itself runs as the klai superuser in
``post_deploy_f4a8c2e6b1d9_widget_messages_feedback.sql`` because
``widget_messages`` is klai-owned and FORCE-RLS (see
post_deploy_a4f72e913c8b_widget_conversations_rls.sql): portal_api — the
role alembic runs as — cannot ALTER TABLE it. Same marker pattern as
57b2c33efe55 / 48fce7e310d6 / b7c1d2e3f4a5.

This file exists only so alembic can advance its head past b7c1d2e3f4a5.

Revision ID: f4a8c2e6b1d9
Revises: b7c1d2e3f4a5
Create Date: 2026-09-04
"""

from __future__ import annotations

revision = "f4a8c2e6b1d9"
down_revision = "b7c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling
    post_deploy SQL by the klai superuser.
    """
    # Intentionally empty: see post_deploy_f4a8c2e6b1d9_widget_messages_feedback.sql


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
    pass
