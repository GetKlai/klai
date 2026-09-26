"""portal_orgs.support_reanalysis_requested_at: durable support reanalysis request

A connector sync that changed knowledge requests an org-wide support-case
reanalysis, debounced so several syncs of one night coalesce into one run.
The request used to be an in-process timer, which a portal-api restart
dropped; it is now a timestamp on the org row that the support reanalysis
loop (``gap_rescorer.support_reanalysis_loop``) picks up once it is older
than the debounce, and clears after the run.

The DDL runs post-deploy as the ``klai`` superuser via
``post_deploy_4d7e1a9c2b63_support_reanalysis_request.sql``, the same route
as 839f2c3165ba: that works whichever role owns ``portal_orgs``, and
``deploy-portal-api.sh`` applies it before the new portal-api container
starts, so no code that reads the column runs before it exists. The column is
nullable with no default, a metadata-only change.
"""

from __future__ import annotations

revision: str = "4d7e1a9c2b63"
down_revision: str | None = "839f2c3165ba"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # No-op: the column is added by the post-deploy SQL named above.
    pass


def downgrade() -> None:
    pass
