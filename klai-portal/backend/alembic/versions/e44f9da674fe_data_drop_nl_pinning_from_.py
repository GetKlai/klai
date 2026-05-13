"""data: drop NL pinning from klantenservice default template

Revision ID: e44f9da674fe
Revises: a5b8c2d6e1f3
Create Date: 2026-05-07

Companion to SPEC-RAG-MULTILINGUAL-CHAT-001 cleanup
(commit a0d72cea, 2026-05-07).

The actual cross-tenant UPDATE lives in
``post_deploy_e44f9da674fe.sql`` because ``portal_templates`` is RLS-
protected with the strict Cat-D pattern (no ``IS NULL`` branch on the
USING clause). Alembic runs as the ``portal_api`` role, which under
that policy sees zero rows when no tenant GUC is set — so a simple
``op.execute("UPDATE portal_templates ...")`` here would silently
no-op across every tenant.

The post-deploy script runs as the ``klai`` superuser (via
``scripts/apply_post_deploy_sql.sh``), bypasses RLS, and is
idempotent (only updates rows still containing the legacy NL-pinning
substring).

This forward migration is therefore intentionally a no-op — its only
purpose is to advance ``alembic_version`` so the chain has a place to
hang the post-deploy SQL.
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "e44f9da674fe"
down_revision: str | Sequence[str] | None = "a5b8c2d6e1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op forward — see post_deploy_e44f9da674fe.sql."""


def downgrade() -> None:
    """No-op reverse — the data fix is one-way.

    Reverting would mean re-introducing the NL-pinning string, which
    has no production value. If a real rollback is ever needed, write
    a sibling post_deploy_rollback_e44f9da674fe.sql by hand.
    """
