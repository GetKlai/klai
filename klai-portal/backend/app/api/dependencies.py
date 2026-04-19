"""Shared FastAPI dependencies."""

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user_id
from app.api.bearer import bearer as bearer  # re-export for routes that import from here
from app.core.database import get_db, set_tenant
from app.models.groups import PortalGroup, PortalGroupMembership
from app.models.portal import PortalOrg, PortalUser
from app.services.entitlements import get_effective_products
from app.services.zitadel import zitadel


def require_product(product: str):
    """Return a FastAPI dependency callable that raises 403 if user lacks the product.

    Org admins bypass the check and always have access to all products.
    """

    async def dependency(
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        role_result = await db.execute(select(PortalUser.role).where(PortalUser.zitadel_user_id == user_id))
        if role_result.scalar_one_or_none() == "admin":
            return
        products = await get_effective_products(user_id, db)
        if product not in products:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Product access required: {product}",
            )

    return dependency


# @MX:ANCHOR fan_in=8
async def _get_caller_org(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> tuple[str, PortalOrg, PortalUser]:
    """Validate token, return (zitadel_user_id, PortalOrg, caller PortalUser)."""
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    zitadel_user_id = info.get("sub")
    if not zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user found in token")

    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")

    org, caller_user = row
    await set_tenant(db, org.id)
    structlog.contextvars.bind_contextvars(org_id=str(org.id), user_id=zitadel_user_id)
    return zitadel_user_id, org, caller_user


# @MX:ANCHOR fan_in=8
def _require_admin(caller_user: PortalUser) -> None:
    """Raise 403 if the caller is not an admin."""
    if caller_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: admin role required")


def _require_admin_or_group_admin_role(caller_user: PortalUser) -> None:
    """Raise 403 unless caller is org admin or has group-admin role."""
    if caller_user.role in ("admin", "group-admin"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied: admin or group admin role required",
    )


async def _require_admin_or_group_admin(
    group_id: int,
    caller_user: PortalUser,
    db: AsyncSession,
) -> None:
    """Raise 403 unless caller may manage members of this group.

    Rules:
    - Org admin (role='admin'): may manage any group, including system groups.
    - group-admin role: may manage any non-system group.
    - System groups (system_key IS NOT NULL): only org admins may manage members.
    """
    if caller_user.role == "admin":
        return

    # Block access to system groups for everyone except org admin
    group_result = await db.execute(select(PortalGroup.system_key).where(PortalGroup.id == group_id))
    system_key = group_result.scalar_one_or_none()
    if system_key is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: system groups can only be managed by org admins",
        )

    if caller_user.role != "group-admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: admin or group admin role required",
        )


async def _require_admin_or_group_manager(
    caller_user: PortalUser,
    org_id: int,
    db: AsyncSession,
) -> None:
    """Raise 403 unless caller is org admin, group-admin, or member of the Group Management system group."""
    if caller_user.role in ("admin", "group-admin"):
        return

    # Check if caller is in the Group Management system group for their org
    gm_result = await db.execute(
        select(PortalGroup.id).where(
            PortalGroup.org_id == org_id,
            PortalGroup.system_key == "group_management",
        )
    )
    gm_group_id = gm_result.scalar_one_or_none()
    _no_access = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied: admin or group admin role required",
    )
    if not gm_group_id:
        raise _no_access

    member_result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == gm_group_id,
            PortalGroupMembership.zitadel_user_id == caller_user.zitadel_user_id,
        )
    )
    if not member_result.scalar_one_or_none():
        raise _no_access
