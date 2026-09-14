"""widget_conversations: visitor_name / visitor_email marker.

The widget's pre-chat step asks the visitor for a name and an e-mail so a
reviewer can send a correction when an answer turns out to be wrong. Those
values used to be prepended to the first user message as a "Visitor details:"
block, which put them in the model prompt and made
``widget_conversations.first_user_query`` (and therefore the activity list and
the top-queries aggregate) show the contact block instead of the question.
They now live in two columns on the conversation row instead.

``widget_conversations`` is klai-owned, so portal_api cannot ALTER it; the DDL
runs as the klai superuser via
``post_deploy_a1c4e7b2d9f3_widget_conversations_visitor_contact.sql``. This
file exists only so alembic can advance its head past d3c8b6a5f1e0.

Revision ID: a1c4e7b2d9f3
Revises: d3c8b6a5f1e0
Create Date: 2026-09-14
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "a1c4e7b2d9f3"
down_revision: str | None = "d3c8b6a5f1e0"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling post_deploy SQL."""
    pass


def downgrade() -> None:
    """No-op marker. The schema rollback is applied via SQL as klai superuser."""
    pass
