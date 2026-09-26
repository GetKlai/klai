"""conversation_quality_judgments: bound the judge retry loop

Production incident (26 Sep 2026): a LibreChat conversation whose judge
response failed to parse (or whose LLM call failed) was never written, so
the org-discovery/exclude queries kept treating it as "not yet judged" and
the next pass (every 30 min, all night) attempted it again — 24 parse
failures against a handful of conversations, night after night, for no new
information each time. The webchat pass (``conversation_judge.py``) has the
same fail-open-and-write-nothing shape and would loop the same way given a
conversation that hits it.

Adds a bounded-attempt marker to the existing one-row-per-conversation
table instead of a separate table: a failed attempt UPSERTs a row with
``outcome`` NULL and ``failed_attempts`` incremented; once that reaches
``_MAX_JUDGE_ATTEMPTS`` (conversation_judge.py) the conversation is excluded
by the same query that already excludes a successfully judged one. Requires
``outcome``/``confidence``/``judged_at`` to become nullable (a row can now
exist that failed every attempt and was never actually judged).

Same klai-owned + Cat-D RLS shape as the rest of this table (portal_api
lacks ALTER privilege) — DDL runs post-deploy via
``post_deploy_839f2c3165ba_conversation_quality_judge_attempts.sql`` as the
``klai`` superuser. RLS is untouched: the existing tenant_isolation policy
already covers new columns on the same table.
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision: str = "839f2c3165ba"
down_revision: str | None = "i5c6t7u8r9n0"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op, same reason as every prior revision on this klai-owned table:
    # portal_api lacks ALTER privilege on conversation_quality_judgments.
    pass


def downgrade() -> None:
    pass
