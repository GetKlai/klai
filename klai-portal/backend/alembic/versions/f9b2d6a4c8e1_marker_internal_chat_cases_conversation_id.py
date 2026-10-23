"""internal_chat_cases keyed by LibreChat's conversation id — marker.

The case an internal conversation carries to its next turn was keyed by
digests of the employee's messages, because LibreChat sent no conversation id.
The "Klai AI" endpoint now sends it as ``X-Klai-Conversation-Id``, so the row
is keyed by ``(org_id, user_id, conversation_id)`` and ``conversation_key`` and
``turn_digest`` go.

``internal_chat_cases`` is owned by ``klai`` (see
``post_deploy_f3a9c6e2b4d1_internal_chat_cases_rls.sql``), so portal_api cannot
ALTER it; the change is applied as the klai superuser by
``post_deploy_f9b2d6a4c8e1_internal_chat_cases_conversation_id.sql``. This
file exists only so alembic can advance its head past a7d2e9c4f1b8.

Revision ID: f9b2d6a4c8e1
Revises: a7d2e9c4f1b8
Create Date: 2026-10-22
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "f9b2d6a4c8e1"
down_revision: str | None = "a7d2e9c4f1b8"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling post_deploy SQL."""


def downgrade() -> None:
    """No-op marker. The digest columns cannot be refilled; the old code would need an empty table with them."""
