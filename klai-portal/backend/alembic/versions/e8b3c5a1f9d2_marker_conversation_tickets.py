"""widget_ticket_settings + conversation_tickets — SPEC-KNOWLEDGE-ESCALATION-001 §4.1 (marker revision)

Reserves a revision slot for the two ticket tables: the per-widget HubSpot
ticket settings (encrypted service key + targets) and one row per ticket a
reviewer created (or tried to create) from a reviewed conversation.

Same klai-owned + Cat-D RLS shape as ``answer_reviews``
(``c2a7e9d4b1f6_marker_answer_reviews.py``): RLS policies are NOT created
here because ``portal_api`` is not the table owner. All DDL is applied
post-deploy via ``post_deploy_e8b3c5a1f9d2_conversation_tickets_rls.sql`` as
the ``klai`` superuser.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "e8b3c5a1f9d2"
down_revision: str | None = "4d7e1a9c2b63"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op: portal_api lacks REFERENCES privilege on `widgets`,
    # `widget_conversations`, `portal_orgs` and `portal_users` (all owned by
    # klai), so CREATE TABLE with those foreign keys fails with 42501 when
    # alembic runs as portal_api. All DDL for this revision lives in
    # post_deploy_e8b3c5a1f9d2_conversation_tickets_rls.sql, applied as klai
    # superuser AFTER alembic upgrade head completes successfully.
    pass


def downgrade() -> None:
    pass
