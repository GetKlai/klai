"""Effective product entitlement resolution.

Computes the union of plan-included, direct (portal_user_products), and
group-inherited (portal_group_products via portal_group_memberships)
product assignments.
"""

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.core.plans import ADDON_PRODUCTS, get_plan_products
from app.models.groups import PortalGroupMembership, PortalGroupProduct
from app.models.portal import PortalOrg, PortalUser
from app.models.products import PortalUserProduct


# @MX:ANCHOR fan_in=4 — called from /api/me, /internal/knowledge-feature-check,
#   dependencies.require_product, and SPEC-SEC-022 gating. Signature changes ripple.
# @MX:REASON self-healing tenant context is a load-bearing contract: callers (especially
#   /internal/* and FastAPI dependencies resolved in parallel with _get_caller_org)
#   rely on this function to set_tenant itself via the portal_users permissive lookup.
#   Removing that behaviour re-introduces the 2026-04-21 Voys incident class.
# @MX:NOTE Plan-included products (chat, knowledge) are the floor for any user on a paying
#   plan. Without this union, sidebars and gates render empty for admins/users without an
#   explicit per-user/per-group product entitlement — which broke the post-Phase-3 UI.
#   Add-on products (scribe, docs) are filtered against org.enabled_addons: an entitlement
#   row whose tenant toggle is off becomes dormant — kept in DB to preserve manual admin
#   work, but excluded from effective products so the sidebar/gates honor the toggle.
# @MX:SPEC SPEC-SEC-007, SPEC-PORTAL-PROFILES-001
async def get_effective_products(zitadel_user_id: str, db: AsyncSession) -> list[str]:
    """Return all products a user has access to.

    Three sources, all unioned then filtered:
      1. Plan-included products (from org.plan via PLAN_PRODUCTS) — the floor for anyone on a paying plan.
      2. Direct per-user assignments (portal_user_products) — explicitly granted to this user.
      3. Group-inherited assignments (portal_group_products via portal_group_memberships).

    Add-on products (ADDON_PRODUCTS — scribe, docs) are then filtered against
    `portal_orgs.enabled_addons`: a row whose tenant toggle is off becomes
    dormant. This keeps the SPEC-PORTAL-PROFILES-001 two-layer gate consistent
    between `require_product` (API gating) and `/api/me` (sidebar gating).

    Self-heals tenant context: looks up the user's org_id and calls
    set_tenant() before querying the RLS-protected tables. This lets callers
    invoke this function without being responsible for set_tenant — e.g.
    FastAPI dependency ordering means `require_product` can resolve BEFORE
    `_get_caller_org` has run, and /internal endpoints that don't carry a
    request org_id can still resolve entitlements.

    Returns empty list if the user has no portal row yet (pre-provisioning
    or deleted user).
    """
    # Resolve user's tenant + plan + enabled add-ons. portal_users has a
    # permissive-on-missing policy so this lookup is safe without prior set_tenant().
    org_row = await db.execute(
        select(PortalUser.org_id, PortalOrg.plan, PortalOrg.enabled_addons)
        .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = org_row.one_or_none()
    if row is None:
        return []
    org_id, plan, enabled_addons = row
    await set_tenant(db, org_id)

    # Plan-included products (free / core / professional / complete → chat, knowledge, …)
    plan_products: set[str] = set(get_plan_products(plan))

    # Direct assignments
    direct_q = select(PortalUserProduct.product).where(PortalUserProduct.zitadel_user_id == zitadel_user_id)

    # Group-inherited assignments
    group_q = (
        select(PortalGroupProduct.product)
        .join(
            PortalGroupMembership,
            PortalGroupProduct.group_id == PortalGroupMembership.group_id,
        )
        .where(PortalGroupMembership.zitadel_user_id == zitadel_user_id)
    )

    combined = union(direct_q, group_q)
    result = await db.execute(combined)
    user_and_group_products: set[str] = set(result.scalars().all())

    effective = plan_products | user_and_group_products

    # Dormancy filter: an add-on entitlement only counts when the tenant
    # toggle is on. Non-add-on products (chat, knowledge) bypass this.
    enabled_addon_set: set[str] = set(enabled_addons or [])
    effective = {p for p in effective if p not in ADDON_PRODUCTS or p in enabled_addon_set}

    return sorted(effective)
