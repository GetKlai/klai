"""Service functions for system-group side-effects.

SPEC-PORTAL-PROFILES-001 Phase 2 P2.5.

When a user is added to a role-bind system-group (system_key prefix "role_"),
their portal_users.role is updated to match the group's role binding.

Add-on system-groups (system_key prefix "addon_") grant products via
portal_group_products (existing entitlement mechanism) — no extra logic here.
"""

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.system_groups import SYSTEM_GROUP_ROLE_MAP
from app.models.groups import PortalGroup
from app.models.portal import PortalUser

logger = structlog.get_logger()


async def sync_role_from_system_group(
    zitadel_user_id: str,
    group_id: int,
    db: AsyncSession,
) -> str | None:
    """Update portal_users.role when user joins a role-bind system-group.

    # @MX:NOTE: SPEC-PORTAL-PROFILES-001 Phase 2 P2.5 — NEW behaviour.
    # Previously, system-groups only granted products. Now role-bind groups
    # (system_key prefix "role_") also update portal_users.role on membership-add.
    # This is the single authoritative path for role updates via group membership.
    # Direct role changes still go through PATCH /api/admin/users/{id}/role.

    Returns the new role string if a role was set, None otherwise.
    """
    # Load group and check it's a role-bind system group
    group_result = await db.execute(select(PortalGroup).where(PortalGroup.id == group_id))
    group = group_result.scalar_one_or_none()
    if group is None or not group.is_system:
        return None

    role = SYSTEM_GROUP_ROLE_MAP.get(group.system_key or "")
    if role is None:
        return None

    # Update the user's role
    result = await db.execute(
        update(PortalUser)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
        .values(role=role)
        .returning(PortalUser.id)
    )
    updated = result.fetchone()
    if updated:
        logger.info(
            "system_group.role_synced",
            group_id=group_id,
            system_key=group.system_key,
            role=role,
            user_id=zitadel_user_id,
        )
    return role
