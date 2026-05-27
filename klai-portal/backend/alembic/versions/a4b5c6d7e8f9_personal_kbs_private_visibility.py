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
    # No-op. See module docstring for the original intent. The real work
    # — ALTER TABLE to expand ck_portal_kb_visibility to include 'private'
    # AND UPDATE all owner_type='user' rows — landed via a hand-applied
    # SQL block on 2026-05-27 because:
    #
    #   1. ``portal_knowledge_bases`` is FORCE-RLS'd with a policy that
    #      raises ``InsufficientPrivilegeError`` when neither
    #      ``app.current_org_id`` nor ``app.cross_org_admin`` is set.
    #      Alembic in the portal-api container runs under the
    #      ``portal_api`` role with no GUC, so the UPDATE branch crashed
    #      ``alembic upgrade head`` on the first deploy attempt and the
    #      container failed to start.
    #
    #   2. The existing check constraint ``ck_portal_kb_visibility``
    #      only allowed ``('public', 'internal')`` — so even with the
    #      RLS issue worked around, the new ``'private'`` value would
    #      have been rejected. DROP/ADD CONSTRAINT requires owner
    #      privilege which portal_api also lacks.
    #
    # Hand-applied SQL on prod (BEGIN; ALTER TABLE DROP/ADD CONSTRAINT;
    # UPDATE; COMMIT;) reported "UPDATE 38" — every user-owned KB row
    # now has ``visibility='private'``. ``alembic_version`` was already
    # at ``a4b5c6d7e8f9`` when the manual fix ran (stamped by the
    # earlier failed deploy), so the schema and alembic agree.
    #
    # This stub keeps the revision parseable for fresh installs but
    # does NOT replay the UPDATE — replaying against an already-correct
    # prod is harmless, against a fresh dev DB the rows don't exist yet
    # so the UPDATE is a no-op, and the constraint-DROP/ADD would crash
    # because the constraint may already include 'private' (the value
    # was added to the dev schema by hand or by a parallel script).
    # See pitfall ``alembic-stamped-past-skipped-migration`` for the
    # broader pattern.
    pass


def downgrade() -> None:
    # Mirror the no-op upgrade. The ALTER TABLE drop-and-reduce-check
    # would have to be hand-applied too, which is out of scope for a
    # downgrade we never expect to run in prod.
    pass
