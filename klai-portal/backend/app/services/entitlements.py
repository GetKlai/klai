"""Effective product entitlement resolution.

Products are derived from (role, seat_type, platform_unlocked_features).
``portal_orgs.plan`` is legacy billing/quota state and is not an entitlement
axis. No reads on portal_user_products / portal_group_products. Single SELECT
against the permissive portal_users + portal_orgs tables — no tenant-context
dance needed.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.features import derive_user_products
from app.models.portal import PortalOrg, PortalUser


# @MX:ANCHOR fan_in=4 -- called from /api/me, /internal/users/{id}/products,
#   dependencies.require_product, and SPEC-SEC-022 gating. Signature stable.
# @MX:NOTE SPEC-PORTAL-EXTENSIONS-UNIFY-001: derivation is account type OR
#   platform_unlocked_features gated by FEATURE_MIN_PROFILE. No per-user /
#   per-group feature tables. No tenant-context self-healing — both
#   portal_users and portal_orgs are permissive on the zitadel-user-id lookup
#   so this is RLS-safe without set_tenant.
# @MX:SPEC SPEC-PORTAL-RBAC-001
async def get_effective_products(zitadel_user_id: str, db: AsyncSession) -> list[str]:
    """Return all products a user has access to.

    Single SELECT pulls (role, seat_type, platform_unlocked_features); pure-Python
    derivation produces the answer. Returns empty list if user has no
    portal row yet (pre-provisioning or deleted user).
    """
    result = await db.execute(
        select(PortalUser.role, PortalUser.seat_type, PortalOrg.platform_unlocked_features)
        .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = result.one_or_none()
    if row is None:
        return []
    role, seat_type, unlocked = row
    return sorted(derive_user_products(role, seat_type, unlocked or []))
