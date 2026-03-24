"""Effective product entitlement resolution.

Computes the union of direct (portal_user_products) and group-inherited
(portal_group_products via portal_group_memberships) product assignments.
"""

from sqlalchemy import select, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.groups import PortalGroupMembership, PortalGroupProduct
from app.models.products import PortalUserProduct


async def get_effective_products(zitadel_user_id: str, db: AsyncSession) -> list[str]:
    """Return all products a user has access to (direct + group-inherited)."""
    # Direct assignments
    direct_q = select(PortalUserProduct.product).where(
        PortalUserProduct.zitadel_user_id == zitadel_user_id
    )

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
