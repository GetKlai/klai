"""widget conversation audit-trail tables

Revision ID: a4f72e913c8b
Revises: z3a4b5c6d7e8
Create Date: 2026-05-21

Adds two tables to record every chat turn that flows through
`/partner/v1/chat/completions` for a widget. The admin UI surfaces
these on the new "Activiteit" tab of the widget detail page so the
owner can review what people have asked the bot and how it
responded.

Tables live in the public schema next to ``widgets`` /
``widget_kb_access`` so the RLS helper ``_rls_current_org_id()`` is
the natural fit (Cat-D policy shape per
``.claude/rules/klai/projects/portal-security.md``).

RLS policies are NOT created here — ``portal_api`` is not the table
owner. They are applied post-deploy via
``post_deploy_a4f72e913c8b_widget_conversations_rls.sql`` as the
``klai`` superuser. See
``.claude/rules/klai/pitfalls/process-rules.md::alembic-cannot-drop-non-portal_api-tables``.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "a4f72e913c8b"
down_revision: str | None = "f1ff304b7b0a"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op: portal_api lacks REFERENCES privilege on `widgets` (owned
    # by klai), so CREATE TABLE … FOREIGN KEY(widget_id) REFERENCES
    # widgets(id) fails with 42501 when alembic runs as portal_api.
    # All DDL for this revision lives in
    # post_deploy_a4f72e913c8b_widget_conversations_rls.sql which is
    # applied by an operator (or scripts/apply_post_deploy_sql.sh) as
    # klai superuser AFTER alembic upgrade head completes successfully.
    #
    # Same pattern as RLS DDL on Cat-D tables — see
    # `.claude/rules/klai/pitfalls/process-rules.md` →
    # alembic-cannot-drop-non-portal_api-tables.
    pass


def downgrade() -> None:
    pass
