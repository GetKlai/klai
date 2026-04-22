"""Effective product entitlement resolution.

Computes the union of direct (portal_user_products) and group-inherited
(portal_group_products via portal_group_memberships) product assignments.
"""

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.models.groups import PortalGroupMembership, PortalGroupProduct
from app.models.portal import PortalUser
from app.models.products import PortalUserProduct


# @MX:ANCHOR fan_in=4 — called from /api/me, /internal/knowledge-feature-check,
#   dependencies.require_product, and SPEC-SEC-022 gating. Signature changes ripple.
# @MX:REASON self-healing tenant context is a load-bearing contract: callers (especially
#   /internal/* and FastAPI dependencies resolved in parallel with _get_caller_org)
#   rely on this function to set_tenant itself via the portal_users permissive lookup.
#   Removing that behaviour re-introduces the 2026-04-21 Voys incident class.
# @MX:SPEC SPEC-SEC-007
async def get_effective_products(zitadel_user_id: str, db: AsyncSession) -> list[str]:
    """Return all products a user has access to (direct + group-inherited).

    Self-heals tenant context: looks up the user's org_id and calls
    set_tenant() before querying the RLS-protected portal_user_products
    and portal_group_products. This lets callers invoke this function
    without being responsible for set_tenant — e.g. FastAPI dependency
    ordering means `require_product` can resolve BEFORE `_get_caller_org`
    has run, and /internal endpoints that don't carry a request org_id
    can still resolve entitlements.

    Returns empty list if the user has no portal row yet (pre-provisioning
    or deleted user).
    """
    # Resolve user's tenant. portal_users has a permissive-on-missing policy
    # so this lookup is safe without prior set_tenant().
    org_row = await db.execute(select(PortalUser.org_id).where(PortalUser.zitadel_user_id == zitadel_user_id))
    org_id = org_row.scalar_one_or_none()
    if org_id is None:
        return []
    await set_tenant(db, org_id)

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
    return list(result.scalars().all())
