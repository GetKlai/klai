"""SPEC-PORTAL-RBAC-001: drop legacy product entitlement data + system groups

Revision ID: rbac001drop00
Revises: fd9c4a39d14b
Create Date: 2026-05-04

SPEC-PORTAL-RBAC-001 v0.2.0 collapses three overlapping mechanisms into the
canonical three-concept SaaS model (workspace features = plan + add-ons,
user permissions = profile, groups = content scoping). After this migration:

* portal_user_products is empty -- products are now derived from
  (role, plan, enabled_addons) at read time. The TABLE is kept on the
  schema for a future per-seat-billing SPEC.
* portal_group_products is empty -- per-group product assignment is gone.
  Same rationale as above for keeping the table.
* All system groups are removed. Their memberships cascade-delete via the
  FK on portal_group_memberships.group_id (existing CASCADE rule).

This migration is destructive. The user explicitly accepted the risk
(SPEC-PORTAL-RBAC-001 sparring decision #5: "C. Direct opruimen bij deploy")
because the system is still in development and there is no production
external-customer dependency on the affected data.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "rbac001drop00"
down_revision: str | None = "fd9c4a39d14b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Alembic runs as the portal_api user; the RLS guard installed by
    # post_deploy_rls_raise_on_missing_context.sql refuses any DML on
    # category-D tables (portal_user_products, portal_group_products,
    # portal_groups, portal_group_memberships) unless either
    # `app.current_org_id` is set or `app.cross_org_admin = 'true'`. This
    # migration is an org-wide cleanup, so the right opt-out is the
    # cross-org admin marker.
    #
    # SET LOCAL keeps the marker scoped to this migration's transaction;
    # the connection is released back to the pool with the marker cleared
    # automatically when the transaction commits/rolls back. The opt-out
    # is consumed by the RLS guard at the moment of each DELETE below.
    op.execute(sa.text("SET LOCAL app.cross_org_admin = 'true'"))

    # Sequencing: cascade-delete via group_id FK runs first, so memberships
    # in role_* / addon_* groups disappear when their parent group rows go.
    # We use plain DELETE rather than TRUNCATE to honour any non-CASCADE FKs
    # that future tables might add (defence in depth).
    op.execute(sa.text("DELETE FROM portal_user_products"))
    op.execute(sa.text("DELETE FROM portal_group_products"))
    op.execute(
        sa.text(
            "DELETE FROM portal_group_memberships "
            "WHERE group_id IN (SELECT id FROM portal_groups WHERE system_key IS NOT NULL)"
        )
    )
    op.execute(sa.text("DELETE FROM portal_groups WHERE system_key IS NOT NULL"))


def downgrade() -> None:
    # The deleted data cannot be reconstructed; downgrade is a no-op.
    # Recovering would require restoring from backup. SPEC explicitly
    # accepts this trade-off (one-way migration during development).
    pass
