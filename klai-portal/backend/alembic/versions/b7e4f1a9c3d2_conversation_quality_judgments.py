"""conversation_quality_judgments — SPEC-CHAT-QUALITY-LOOP-001 REQ-1

Adds one table storing the nightly LLM-as-judge verdict for a webchat
conversation: outcome, failure category, evidence-grounded reasoning,
confidence and a suggested follow-up action. Sits next to
``widget_conversations`` as an enrichment layer on top of the existing
heuristic ``widget_conversations.outcome`` column — that column is
unchanged and keeps serving as the cheap same-day fallback.

Scope is webchat-only for now (REQ-1 ties directly to
``widget_conversations`` via FK); LibreChat is explicitly deferred pending
the cross-tenant Mongo-read spike in SPEC-CHAT-QUALITY-LOOP-001 §9.1.

Tables live in the public schema next to ``widget_conversations``, same
klai-owned + Cat-D RLS shape (per
``.claude/rules/klai/projects/portal-security.md``). RLS policies are NOT
created here — ``portal_api`` is not the table owner. They are applied
post-deploy via
``post_deploy_b7e4f1a9c3d2_conversation_quality_judgments_rls.sql`` as the
``klai`` superuser, same pattern as
``post_deploy_a4f72e913c8b_widget_conversations_rls.sql``.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "b7e4f1a9c3d2"
down_revision: str | None = "e81c2f93a640"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op: portal_api lacks REFERENCES privilege on `widget_conversations`
    # (owned by klai), so CREATE TABLE ... FOREIGN KEY(conversation_id)
    # REFERENCES widget_conversations(id) fails with 42501 when alembic
    # runs as portal_api. All DDL for this revision lives in
    # post_deploy_b7e4f1a9c3d2_conversation_quality_judgments_rls.sql,
    # applied by an operator (or scripts/apply_post_deploy_sql.sh) as klai
    # superuser AFTER alembic upgrade head completes successfully.
    pass


def downgrade() -> None:
    pass
