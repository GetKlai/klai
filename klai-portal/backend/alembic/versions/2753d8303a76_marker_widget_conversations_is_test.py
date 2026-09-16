"""widget_conversations.is_test — marker.

The reviewer-facing "mark as test message" action (SPEC-KNOWLEDGE-ACTIVITY-001,
PUT /api/app/activity/conversations/{id}/test) needs its own column: an
earlier version of this feature reused ``is_preview``, which review rejected
because that column already means one specific thing — an admin's own preview
session in the widget — and overloading it made every reader of ``is_preview``
implicitly also a reader of the reviewer test-mark, with no way to tell the
two apart from the column alone.

``widget_conversations`` is klai-owned, so portal_api cannot ALTER it; the DDL
runs as the klai superuser via
``post_deploy_2753d8303a76_widget_conversations_is_test.sql``. This file
exists only so alembic can advance its head past 63e87b89aa64.

Revision ID: 2753d8303a76
Revises: 63e87b89aa64
Create Date: 2026-09-16
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "2753d8303a76"
down_revision: str | None = "63e87b89aa64"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling post_deploy SQL."""
    pass


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
    pass
