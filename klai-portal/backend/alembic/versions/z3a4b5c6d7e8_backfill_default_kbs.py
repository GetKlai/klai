"""backfill default org + personal KBs for existing tenants (no-op since 2026-05-13)

Revision ID: z3a4b5c6d7e8
Revises: z2a3b4c5d6e7
Create Date: 2026-04-13

This migration originally backfilled default 'org' and 'personal-{user_id}'
knowledge-base rows for every existing tenant when SPEC-KB-AND-DOCS-LIBRARIES
landed (2026-04-13). The INSERT references columns ``visibility``,
``docs_enabled``, ``owner_type``, ``default_org_role``, ``owner_user_id`` —
those columns are added by LATER migrations in the chain, not by
``z2a3b4c5d6e7_add_kb_and_docs_libraries`` (its parent). Postgres parses the
whole INSERT against the schema-at-execution-time, so on a strict topological
upgrade ``alembic upgrade head`` from an empty DB this migration raised
``UndefinedColumnError: column "visibility" of relation "portal_knowledge_bases"
does not exist`` and rolled back the entire upgrade transaction.

Production was never affected because the chain was different when each
SPEC-branch landed — by the time this revision ran on prod, the later
column-adding migrations had already been applied from a parallel branch.
The bug was only visible on fresh installs, which only became a use-case
after SPEC-LOCAL-DEV-001 (2026-05-13) shipped ``make migrate`` in the
standalone local-dev runbook.

Fix: convert ``upgrade()`` to a no-op. Justification:

  1. Prod's alembic_version is far past z3a4b5c6d7e8; this revision is never
     re-run there.
  2. Fresh installs have zero rows in ``portal_orgs`` / ``portal_users``, so
     the original INSERTs would have produced zero rows regardless.
  3. Default KBs for newly-created orgs/users are created lazily by the
     application via ``app.services.default_knowledge_bases``:
       - ``resolve_org_kb`` → ``create_default_org_kb`` (4 call-sites)
       - ``resolve_personal_kb`` → ``create_default_personal_kb``
     so no backfill is needed on signup/seed paths.
  4. ``downgrade()`` is preserved as-is — it only references ``slug``, a
     column that exists from the parent migration ``z2a3b4c5d6e7``.

See ``.claude/rules/klai/pitfalls/process-rules.md`` →
``alembic-multi-pr-head-split`` for the pattern that caused the original
forward-column-reference, and the runbook
``docs/runbooks/local-dev.md`` troubleshooting section.
"""

from alembic import op

revision = "z3a4b5c6d7e8"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op. See module docstring for rationale.
    pass


def downgrade() -> None:
    # Preserved from the original migration. Both DELETEs reference only `slug`,
    # which exists from z2a3b4c5d6e7 onwards, so this remains parseable even on
    # downgrades from older schema states.
    op.execute("""
        DELETE FROM portal_knowledge_bases WHERE slug = 'org'
    """)
    op.execute("""
        DELETE FROM portal_knowledge_bases WHERE slug LIKE 'personal-%'
    """)
