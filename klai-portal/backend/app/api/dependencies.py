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
    PROFILE_LADDER,
    Capability,
    effective_role,
)
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
    """Raise 403 unless caller has group_manager or admin profile role.

    REQ-7: group_manager and above may manage groups.
    kb_manager does NOT have group management rights.
    """
    role = caller_user.role
    role_idx = PROFILE_LADDER.index(role) if role in PROFILE_LADDER else -1
    required_idx = PROFILE_LADDER.index("group_manager")
    if role_idx < required_idx:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: group_manager role or higher required",
        )


async def _require_admin_or_group_admin(
    group_id: int,
    caller_user: PortalUser,
    db: AsyncSession,
) -> None:
    """Raise 403 unless caller may manage members of this group.

    Rules:
    - Org admin (role='admin'): may manage any group, including system groups.
    - group_manager role: may manage any non-system group.
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

    role = caller_user.role
    role_idx = PROFILE_LADDER.index(role) if role in PROFILE_LADDER else -1
    required_idx = PROFILE_LADDER.index("group_manager")
    if role_idx < required_idx:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: admin or group_manager role required",
        )


async def _require_admin_or_group_manager(
    caller_user: PortalUser,
    org_id: int,
    db: AsyncSession,
) -> None:
    """Raise 403 unless caller is org admin or group_manager (or higher)."""
    role = caller_user.role
    role_idx = PROFILE_LADDER.index(role) if role in PROFILE_LADDER else -1
    required_idx = PROFILE_LADDER.index("group_manager")
    if role_idx >= required_idx:
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
        detail="Access denied: admin or group_manager role required",
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


def require_at_least_dep(required_role: str):
    """Return a FastAPI dependency that enforces a minimum profile role.

    This is the fully-wired FastAPI version of _require_at_least from profiles.py.
    It resolves the caller via bearer token + DB lookup.

    G3 analysis (SPEC-PORTAL-PROFILES-001 Phase 1.5b): all current group-management
    routes use _require_admin_or_group_admin / _require_admin_or_group_manager which
    additionally check system_key per group_id or system group membership -- logic that
    cannot be expressed as a simple role-ladder Depends.  This function is therefore
    currently not called by any route.  It is retained as the correct dependency factory
    for future routes that need only a role-ladder check (no system-group carve-out).

    Usage::

        @router.delete("/groups/{id}", dependencies=[Depends(require_at_least_dep("group_manager"))])

    @MX:NOTE fan_in=0 -- no routes use this yet; see G3 analysis in docstring.
    """
    required_idx = PROFILE_LADDER.index(required_role)

    async def _check(
        credentials: HTTPAuthorizationCredentials = Depends(bearer),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        _, _, caller_user = await _get_caller_org(credentials, db)
        role = effective_role(caller_user)
        caller_idx = PROFILE_LADDER.index(role) if role in PROFILE_LADDER else -1
        if caller_idx < required_idx:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role {required_role!r} or higher required",
            )

    return _check


async def get_effective_capabilities(user_id: str, db: AsyncSession) -> set[str]:
    """Return the set of KB capabilities for a user.

    effective_capabilities = PROFILE_CAPABILITIES[role] & PLAN_LIMITS[plan].capabilities

    Admin bypass: admin role always gets complete-tier capabilities regardless of plan.
    (Intentional per SPEC-PORTAL-PROFILES-001 v0.2.0: admin must be able to test what
    a plan upgrade unlocks without upgrading the billing plan first.)

    Returns an empty set for unknown users or plans.

    SPEC-PORTAL-PROFILES-001 Phase 1.5 — F3.
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

    # Admin users get the complete-tier capabilities regardless of plan.
    if user.role == "admin":
        return set(PLAN_LIMITS["complete"].capabilities)

    # All other roles: intersect role capabilities with plan capabilities.
    role_caps = PROFILE_CAPABILITIES.get(user.role, frozenset())
    plan_caps = get_plan_limits(org.plan).capabilities
    return set(role_caps) & set(plan_caps)
