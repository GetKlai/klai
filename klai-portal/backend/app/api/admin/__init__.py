"""
Admin API package.
All endpoints require authentication and resolve the caller's org from their OIDC token.
Endpoints are split by domain: users, products, settings, audit.
"""

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bearer import bearer
from app.core.config import settings as _app_settings  # avoid shadow by .settings submodule include
from app.core.database import set_tenant
from app.models.portal import PortalOrg, PortalUser
from app.services.zitadel import zitadel

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def _get_caller_org(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
    *,
    allow_during_deprovisioning: bool = False,
) -> tuple[str, "PortalOrg", "PortalUser"]:
    """Validate token, return (zitadel_user_id, PortalOrg, caller PortalUser).

    # @MX:ANCHOR: fan_in>=6 — called by every admin endpoint. SPEC-INFRA-TENANT-DELETE-001 R1
    #   added allow_during_deprovisioning so the deprovision-status polling endpoint can
    #   still respond while the org is being deleted. All other callers keep the default
    #   (False) and receive 403 tenant_deleting while deprovisioning is in progress.
    """
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        logger.warning("Admin auth: userinfo fetch failed: %s", exc, exc_info=True)
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

    # SPEC-INFRA-TENANT-DELETE-001 R1: block all admin actions while a deprovisioning
    # sequence is running, unless the endpoint explicitly opts in (e.g. status polling).
    if org.provisioning_status == "deprovisioning" and not allow_during_deprovisioning:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tenant_deleting",
                "message": "This organisation is being deleted. No further actions are permitted.",
            },
        )

    await set_tenant(db, org.id)

    # SPEC-INFRA-TENANT-DELETE-001 R6: enable platform-admin RLS on
    # tenant_lifecycle_events when the caller is in the platform org. The
    # post-deploy SQL policy reads `app.is_platform_admin` to gate SELECT —
    # without this assignment, the audit-trail is permanently invisible
    # via the API. SET LOCAL is transaction-scoped so the next request on
    # this pooled connection starts clean (cleared by _reset_tenant_context).
    if org.slug == _app_settings.platform_org_slug:
        # GUC value MUST match the post_deploy SQL policy:
        # `current_setting('app.is_platform_admin', true) = '1'`. Using '1'
        # rather than 'true' aligns with the policy and with the convention
        # used by app.current_org_id (also stored as text). A value mismatch
        # would silently filter the SELECT-policy to zero rows — defeating
        # the whole audit-readability fix.
        await db.execute(text("SELECT set_config('app.is_platform_admin', '1', true)"))

    return zitadel_user_id, org, caller_user


def _require_admin(caller_user: "PortalUser") -> None:
    """Raise 403 if the caller is not an admin."""
    if caller_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: admin role required")


# --- Sub-router inclusion (no prefix on sub-routers!) ---
from .audit import router as audit_router  # noqa: E402
from .deprovision_org import router as deprovision_org_router  # noqa: E402
from .domains import router as domains_router  # noqa: E402
from .join_requests import router as join_requests_router  # noqa: E402
from .products import router as products_router  # noqa: E402
from .retry_provisioning import router as retry_provisioning_router  # noqa: E402
from .settings import router as settings_router  # noqa: E402
from .users import router as users_router  # noqa: E402

router.include_router(users_router)
router.include_router(products_router)
router.include_router(settings_router)
router.include_router(audit_router)
router.include_router(domains_router)
router.include_router(join_requests_router)
router.include_router(retry_provisioning_router)
router.include_router(deprovision_org_router)

__all__ = [
    "_get_caller_org",
    "_require_admin",
    "bearer",
    "router",
]
