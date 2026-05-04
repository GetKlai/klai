"""Effective product entitlement resolution.

SPEC-PORTAL-RBAC-001 v0.2.0: products are derived purely from
(role, plan, enabled_addons). No reads on portal_user_products /
portal_group_products. Single SELECT against the permissive
portal_users + portal_orgs tables — no tenant-context dance needed.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.features import derive_user_products
from app.models.portal import PortalOrg, PortalUser


# @MX:ANCHOR fan_in=4 -- called from /api/me, /internal/users/{id}/products,
#   dependencies.require_product, and SPEC-SEC-022 gating. Signature stable.
# @MX:NOTE SPEC-PORTAL-RBAC-001: derivation is plan OR enabled_addons gated by
#   FEATURE_MIN_PROFILE. No per-user / per-group tables. No tenant-context
#   self-healing -- both portal_users and portal_orgs are permissive on the
#   zitadel-user-id lookup so this is RLS-safe without set_tenant.
# @MX:SPEC SPEC-PORTAL-RBAC-001
async def get_effective_products(zitadel_user_id: str, db: AsyncSession) -> list[str]:
    """Return all products a user has access to.

    Single SELECT pulls (role, plan, enabled_addons); pure-Python
    derivation produces the answer. Returns empty list if user has no
    portal row yet (pre-provisioning or deleted user).
    """
    result = await db.execute(
        select(PortalUser.role, PortalOrg.plan, PortalOrg.enabled_addons)
        .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = result.one_or_none()
    if row is None:
        return []
    role, plan, enabled_addons = row
    return sorted(derive_user_products(role, plan, enabled_addons or []))
