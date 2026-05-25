"""REQ-15 — widget_conversations.is_preview marker.

The DDL itself runs as klai-superuser in
``post_deploy_48fce7e310d6_widget_conversations_is_preview.sql``
because ``widget_conversations`` is klai-owned (created via post-deploy
SQL in earlier SPEC-WIDGET-002 work). portal_api cannot ALTER TABLE on
klai-owned tables — see ``alembic-cannot-drop-non-portal_api-tables``
pitfall.

This file exists only so alembic can advance its head past 45b528904319.

Revision ID: 48fce7e310d6
Revises: 45b528904319
Create Date: 2026-05-25
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "48fce7e310d6"
down_revision = "45b528904319"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling
    post_deploy SQL by the klai superuser.
    """
    # Intentionally empty: see post_deploy_48fce7e310d6_widget_conversations_is_preview.sql
    pass


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
    pass
