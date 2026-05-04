"""Shared FastAPI dependencies."""

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user_id
from app.api.bearer import bearer as bearer  # re-export for routes that import from here
from app.core.database import get_db, set_tenant
from app.core.plan_limits import PLAN_LIMITS, get_plan_limits
from app.core.profiles import (
    PROFILE_CAPABILITIES,
    Capability,
)
from app.models.portal import PortalOrg, PortalUser
from app.services.entitlements import get_effective_products
from app.services.zitadel import zitadel


def require_product(product: str):
    """Return a FastAPI dependency callable that raises 403 if user lacks the product.

    SPEC-PORTAL-RBAC-001 v0.2.0: single-layer profile-driven check. The product
    is granted iff (a) it is in the workspace features (plan + enabled add-ons)
    AND (b) the user's profile rank meets FEATURE_MIN_PROFILE for that feature.
    Both conditions are folded into get_effective_products. No admin bypass --
    admin sits at the top of the rank ladder and passes any FEATURE_MIN_PROFILE
    structurally.
    """

    async def dependency(
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        if product not in await get_effective_products(user_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Product not available: {product}",
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


def require_capability(capability: Capability):
    """Return a FastAPI dependency callable that raises 403 when the caller lacks a KB capability.

    Usage::

        @router.get("/some-endpoint", dependencies=[Depends(require_capability("kb.connectors"))])

    Rules (SPEC-PORTAL-PROFILES-001 v0.2.0):
    - Admin users bypass the check (they always have complete-tier capabilities).
    - effective_capabilities = PROFILE_CAPABILITIES[role] & PLAN_LIMITS[plan].capabilities
    - Unknown users or plans are treated as most restrictive (deny).
    """

    async def dep(
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        caps = await get_effective_capabilities(user_id, db)
        if capability not in caps:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "capability_required",
                    "capability": capability,
                },
            )

    return dep


async def get_effective_capabilities(user_id: str, db: AsyncSession) -> set[str]:
    """Return the set of KB capabilities for a user.

    effective_capabilities = PROFILE_CAPABILITIES[role] & PLAN_LIMITS[plan].capabilities

    Admin bypass: admin role always gets complete-tier capabilities regardless of plan.
    (Intentional per SPEC-PORTAL-PROFILES-001 v0.2.0: admin must be able to test what
    a plan upgrade unlocks without upgrading the billing plan first.)

    Returns an empty set for unknown users or plans.

    SPEC-PORTAL-PROFILES-001 Phase 1.5 -- F3.
    """
    result = await db.execute(
        select(PortalUser, PortalOrg)
        .join(PortalOrg, PortalOrg.id == PortalUser.org_id)
        .where(PortalUser.zitadel_user_id == user_id)
    )
    row = result.one_or_none()
    if row is None:
        return set()

    user, org = row

    # @MX:NOTE -- Admin-bypass: an admin role on any plan tier (including "core")
    # receives the full "complete"-tier capability set. This is INTENTIONAL per
    # SPEC-PORTAL-PROFILES-001 v0.2.0 and v0.3.0: an admin must be able to
    # preview / test what a plan upgrade unlocks BEFORE the org commits to the
    # higher billing tier. Without this, a "core"-plan admin who wants to evaluate
    # the connector ecosystem before paying for "complete" is blocked.
    #
    # Trade-off: this gives admins more capabilities than their plan technically
    # pays for. Acceptable because (a) admins are the billing-decision-maker
    # anyway, (b) the per-user capabilities of NON-admin users on the same org
    # are still constrained by the plan ceiling (so a "core"-tenant's regular
    # users still hit the limits).
    #
    # To remove the bypass: replace the "if user.role == 'admin'" branch with
    # the same intersection logic used for other roles. Tests that assert
    # "test_admin_on_core_gets_complete_tier" would need updating.
    if user.role == "admin":
        return set(PLAN_LIMITS["complete"].capabilities)

    # All other roles: intersect role capabilities with plan capabilities.
    role_caps = PROFILE_CAPABILITIES.get(user.role, frozenset())
    plan_caps = get_plan_limits(org.plan).capabilities
    return set(role_caps) & set(plan_caps)
