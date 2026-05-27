"""set visibility='private' for all owner_type='user' KBs

Revision ID: a4b5c6d7e8f9
Revises: z3a4b5c6d7e8
Create Date: 2026-05-27

Fixes a tenant-isolation leak discovered 2026-05-27: when ``portal_knowledge_bases``
rows are auto-provisioned for user-owned KBs (``owner_type='user'``, slug
``personal-<zitadel_user_id>``), the row inherits the default
``visibility='internal'``. Knowledge-ingest reads kb_config.visibility (which
mirrors portal_knowledge_bases.visibility at provisioning time) and stamps
chunks in Qdrant with ``visibility=internal``. Then the retrieval-api
``scope=org/both`` filter only excludes ``visibility=private`` chunks —
meaning a user-owned personal-KB chunk is returned to OTHER users in the
same org via their normal org-scope queries.

Observed concretely in the GetKlai org (org_id=1) on 2026-05-27: a survey of
qdrant for ``kb_slug LIKE 'personal-%'`` returned 379 chunks, all stamped
``visibility=internal``. 5 of those were returned by a probe scope=org query
from a different requester, confirming the leak.

Fix on the ingest+retrieval side (this PR's sibling code changes):
- ``kb_config.get_kb_visibility`` now returns 'private' for any slug
  starting with ``personal-``, regardless of the DB row.
- ``routes/ingest.py`` derives user_id from the slug when the caller did
  not pass it, ensuring chunks carry ownership in their payload.
- ``retrieval_api/services/search.py::_scope_filter`` matches scope=personal
  chunks via either user_id OR kb_slug, so legacy chunks remain visible.

This migration brings the DB row into agreement so future reads
of portal_knowledge_bases.visibility (e.g. for UI display, admin reports,
audit exports) reflect the correct private state. It also normalises
``knowledge.kb_config`` so the underlying cache will not flip back on
NOTIFY-eviction.

Idempotent: the UPDATE only touches rows where visibility != 'private',
and the kb_config UPSERT is conditional on owner_type='user'.
"""

from alembic import op

revision = "a4b5c6d7e8f9"
# Rebased 2026-05-27 from ``z3a4b5c6d7e8`` to ``e8f9a0b1c2d4``: the
# original parent was no longer a head (it had been absorbed into a
# merge migration on a parallel branch), so PR #709 landed a second
# head and CI's "single head" guard fired on the post-merge deploy.
# See pitfall ``alembic-multi-pr-head-split`` for the recovery pattern.
down_revision = "e8f9a0b1c2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bring portal_knowledge_bases into agreement with the new ingest contract.
    op.execute("""
        UPDATE portal_knowledge_bases
           SET visibility = 'private'
         WHERE owner_type = 'user'
           AND visibility <> 'private'
    """)


def downgrade() -> None:
    # Restore the pre-fix default. Note: rollback only undoes the row state;
    # the code-level override in kb_config.get_kb_visibility still forces
    # 'private' on the ingest path. To fully revert the behavioural change
    # both the code and this migration must be rolled back together.
    op.execute("""
        UPDATE portal_knowledge_bases
           SET visibility = 'internal'
         WHERE owner_type = 'user'
           AND visibility = 'private'
    """)
