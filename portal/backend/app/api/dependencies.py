"""Shared FastAPI dependencies."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user_id
from app.core.database import get_db
from app.models.groups import PortalGroupMembership
from app.models.portal import PortalOrg, PortalUser
from app.services.entitlements import get_effective_products
from app.services.zitadel import zitadel

_ADMIN_ROLES = {"admin"}

bearer = HTTPBearer()


def require_product(product: str):
    """Return a FastAPI dependency callable that raises 403 if user lacks the product.

    Org admins bypass the check — they have access to all products.
    """

    async def dependency(
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        result = await db.execute(
            select(PortalUser.role).where(PortalUser.zitadel_user_id == user_id)
        )
        portal_role = result.scalar_one_or_none()
        if portal_role in _ADMIN_ROLES:
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ongeldig token") from exc

    zitadel_user_id = info.get("sub")
    if not zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Geen gebruiker gevonden in token")

    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisatie niet gevonden")

    org, caller_user = row
    return zitadel_user_id, org, caller_user


# @MX:ANCHOR fan_in=8
def _require_admin(caller_user: PortalUser) -> None:
    """Raise 403 if the caller is not an admin."""
    if caller_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang: admin rechten vereist")


def _require_admin_or_group_admin_role(caller_user: PortalUser) -> None:
    """Raise 403 unless caller is org admin or has group-admin role."""
    if caller_user.role in ("admin", "group-admin"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Geen toegang: admin of groepsbeheerder rechten vereist",
    )


async def _require_admin_or_group_admin(
    group_id: int,
    caller_user: PortalUser,
    db: AsyncSession,
) -> None:
    """Raise 403 unless caller is org admin or group admin for the given group."""
    if caller_user.role in ("admin", "group-admin"):
        return

    result = await db.execute(
        select(PortalGroupMembership).where(
            PortalGroupMembership.group_id == group_id,
            PortalGroupMembership.zitadel_user_id == caller_user.zitadel_user_id,
            PortalGroupMembership.is_group_admin.is_(True),
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Geen toegang: admin of groepsbeheerder rechten vereist",
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
    from app.models.groups import PortalGroup

    gm_result = await db.execute(
        select(PortalGroup.id).where(
            PortalGroup.org_id == org_id,
            PortalGroup.system_key == "group_management",
        )
    )
    gm_group_id = gm_result.scalar_one_or_none()
    _no_access = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Geen toegang: admin of groepsbeheerder rechten vereist",
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
