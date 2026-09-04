"""Widget conversation outcome marker — outcome column on widget_conversations.

The DDL itself runs as the klai superuser in
``post_deploy_d9e0f1a2b3c4_widget_conversations_outcome.sql`` because
``widget_conversations`` is klai-owned and FORCE-RLS (see
post_deploy_a4f72e913c8b_widget_conversations_rls.sql): portal_api — the
role alembic runs as — cannot ALTER TABLE it. Same marker pattern as
a1c2d3e4f5b6 / 48fce7e310d6 / f4a8c2e6b1d9.

This file exists only so alembic can advance its head past f4a8c2e6b1d9.

Revision ID: d9e0f1a2b3c4
Revises: f4a8c2e6b1d9
Create Date: 2026-09-04
"""

from __future__ import annotations

revision = "d9e0f1a2b3c4"
down_revision = "f4a8c2e6b1d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling
    post_deploy SQL by the klai superuser.
    """
    # Intentionally empty: see post_deploy_d9e0f1a2b3c4_widget_conversations_outcome.sql


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
    pass
