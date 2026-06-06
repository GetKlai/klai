"""
Admin API package.
All endpoints require authentication and resolve the caller's org from their OIDC token.
Endpoints are split by domain: users, products, settings, audit.

Auth flow uses ``Depends(get_caller)`` /
``Depends(get_caller_at_least(ProfileRole.ADMIN))`` from
``app.core.permissions`` — see SPEC-PORTAL-RBAC-REFACTOR-001 for the
declarative gate pattern that replaced the legacy
``_get_caller_org`` / ``_require_admin`` helpers previously defined here.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.api.bearer import bearer
from app.core.config import settings as _app_settings  # avoid shadow by .settings submodule include
from app.models.portal import PortalOrg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_platform_admin(caller_org: "PortalOrg") -> None:
    """Raise 403 unless the caller's org is the platform-admin org.

    # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 R1 — platform-admin guard. Uses
    #   _app_settings.platform_org_slug (default 'getklai') to identify the
    #   platform org.
    # @MX:ANCHOR fan_in=3 — every cross-tenant admin endpoint that operates on
    #   a `slug` URL-parameter for an org other than the caller's own MUST
    #   call this guard immediately after `_require_admin`. Failing to do so
    #   is the audit-tenant-isolation-2026-05-05 finding C-2 class.
    """
    if caller_org.slug != _app_settings.platform_org_slug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: platform admin org required",
        )


# --- Sub-router inclusion (no prefix on sub-routers!) ---
from .audit import router as audit_router  # noqa: E402
from .billing import router as billing_router  # noqa: E402
from .deprovision_org import router as deprovision_org_router  # noqa: E402
from .extensions import router as extensions_router  # noqa: E402
from .join_requests import router as join_requests_router  # noqa: E402
from .platform import router as platform_router  # noqa: E402
from .platform_manage import router as platform_manage_router  # noqa: E402
from .platform_messages import router as platform_messages_router  # noqa: E402
from .platform_product_updates import router as platform_product_updates_router  # noqa: E402
from .platform_unlocks import router as platform_unlocks_router  # noqa: E402
from .products import router as products_router  # noqa: E402
from .retry_provisioning import router as retry_provisioning_router  # noqa: E402
from .settings import router as settings_router  # noqa: E402
from .users import router as users_router  # noqa: E402

router.include_router(users_router)
router.include_router(products_router)
router.include_router(settings_router)
router.include_router(audit_router)
router.include_router(join_requests_router)
router.include_router(retry_provisioning_router)
router.include_router(deprovision_org_router)
router.include_router(platform_unlocks_router)
router.include_router(extensions_router)
router.include_router(billing_router)
router.include_router(platform_router)
router.include_router(platform_messages_router)
router.include_router(platform_product_updates_router)
router.include_router(platform_manage_router)

__all__ = [
    "_require_platform_admin",
    "bearer",
    "router",
]
