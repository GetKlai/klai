"""conversation_quality_judgments: add librechat channel — SPEC-CHAT-QUALITY-LOOP-001 REQ-5

Widens the ``channel`` CHECK constraint to allow ``'librechat'`` alongside
``'webchat'``, and adds a nullable ``external_conversation_id`` column for
LibreChat rows: a LibreChat conversation lives in a per-tenant MongoDB
database and is identified by a Mongo ObjectId string, not a row in
``widget_conversations`` — the existing ``conversation_id`` FK cannot
represent it. Webchat rows keep using ``conversation_id`` (FK'd,
NOT NULL for that channel by convention, enforced at the application layer
since a CHECK across channel is awkward with the existing NULL-for-
anonymization design on that same column); LibreChat rows use
``external_conversation_id`` instead and leave ``conversation_id`` NULL.

Same klai-owned + Cat-D RLS shape as the rest of this table. RLS policies
are NOT touched here — the existing tenant_isolation policy already covers
new columns on the same table without a policy change. DDL runs post-deploy
as the ``klai`` superuser via
``post_deploy_d3c8b6a5f1e0_conversation_quality_librechat_channel.sql``.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "d3c8b6a5f1e0"
down_revision: str | None = "b7e4f1a9c3d2"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op, same reason as every prior revision on this klai-owned table:
    # portal_api lacks ALTER privilege on conversation_quality_judgments.
    pass


def downgrade() -> None:
    pass
