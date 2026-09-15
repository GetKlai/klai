"""answer_reviews — SPEC-KNOWLEDGE-ACTIVITY-001 §4.2 (marker revision)

Reserves a revision slot for the `answer_reviews` table: one row per
assistant answer a Klai colleague judged (verdict + cause + note), plus the
retrieval-state snapshot taken at review time so the review stays readable
after the 7-day conversation purge.

Tables live in the public schema next to ``widget_messages``, same klai-owned
+ Cat-D RLS shape (per
``.claude/rules/klai/projects/portal-security.md``). RLS policies are NOT
created here — ``portal_api`` is not the table owner. They are applied
post-deploy via
``post_deploy_c2a7e9d4b1f6_answer_reviews_rls.sql`` as the ``klai`` superuser,
same pattern as
``post_deploy_b7e4f1a9c3d2_conversation_quality_judgments_rls.sql``.

``down_revision`` ``b5d2f8a4c7e1`` comes from the branch that merges before
this one, so it is absent from this worktree by design — run ``alembic heads``
only after both branches are reachable.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "c2a7e9d4b1f6"
down_revision: str | None = "b5d2f8a4c7e1"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op: portal_api lacks REFERENCES privilege on `widget_conversations`,
    # `widget_messages`, `portal_users` and `portal_retrieval_gaps` (all owned
    # by klai), so CREATE TABLE with those foreign keys fails with 42501 when
    # alembic runs as portal_api. All DDL for this revision lives in
    # post_deploy_c2a7e9d4b1f6_answer_reviews_rls.sql, applied by an operator
    # (or scripts/apply_post_deploy_sql.sh) as klai superuser AFTER alembic
    # upgrade head completes successfully.
    pass


def downgrade() -> None:
    pass
