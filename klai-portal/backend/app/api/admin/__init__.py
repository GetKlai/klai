"""
Admin API package.
All endpoints require authentication and resolve the caller's org from their OIDC token.
Endpoints are split by domain: users, products, settings, audit.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portal import PortalOrg, PortalUser
from app.services.zitadel import zitadel

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])
bearer = HTTPBearer()


async def _get_caller_org(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
) -> tuple[str, "PortalOrg", "PortalUser"]:
    """Validate token, return (zitadel_user_id, PortalOrg, caller PortalUser)."""
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        logger.warning("Admin auth: userinfo fetch failed: %s", exc)
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
    return zitadel_user_id, org, caller_user


def _require_admin(caller_user: "PortalUser") -> None:
    """Raise 403 if the caller is not an admin."""
    if caller_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: admin role required")


# --- Sub-router inclusion (no prefix on sub-routers!) ---
from .audit import router as audit_router  # noqa: E402
from .domains import router as domains_router  # noqa: E402
from .join_requests import router as join_requests_router  # noqa: E402
from .products import router as products_router  # noqa: E402
from .settings import router as settings_router  # noqa: E402
from .users import router as users_router  # noqa: E402

router.include_router(users_router)
router.include_router(products_router)
router.include_router(settings_router)
router.include_router(audit_router)
router.include_router(domains_router)
router.include_router(join_requests_router)

__all__ = [
    "_get_caller_org",
    "_require_admin",
    "bearer",
    "router",
]
