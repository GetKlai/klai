"""Central permissions resolver and declarative gate dependencies.

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1.

Single-source-of-truth for "what may this caller do?". One DB query
(``select(PortalOrg, PortalUser).join(...)``) plus pure derivation
produces a frozen ``UserPermissions`` value object that downstream
endpoints consume via FastAPI ``Depends`` dependencies.

Design points:

- One query per request (AC-1). Anything that needs role / plan /
  capabilities / products / kb-limits reads them from the same
  ``UserPermissions`` instance — no second roundtrip.
- Strictly additive: this module does not replace
  ``app/api/dependencies.py::_get_caller_org`` or ``_require_admin``.
  Phase 2 sweeps endpoints over to ``Depends(get_caller_at_least(...))``
  once the new gate-shape has soaked.
- Mirrors the existing ``admin/__init__::_get_caller_org`` semantics for
  RLS tenant binding, deprovisioning-block, and platform-admin GUC, so
  endpoints that switch over keep their behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bearer import bearer
from app.core.config import settings as _app_settings
from app.core.database import get_db, set_tenant
from app.core.features import derive_user_products
from app.core.plan_limits import KBLimits, get_plan_limits
from app.core.profiles import (
    PROFILE_CAPABILITIES,
    PROFILE_LADDER,
    Capability,
    ProfileRole,
    effective_kb_limits,
)
from app.models.portal import PortalOrg, PortalUser
from app.services.zitadel import zitadel

logger = structlog.get_logger()

# O(1) rank lookups keyed by the ProfileRole enum. Values match
# `app.core.profiles.PROFILE_RANK` 1:1 — re-derived here so `permissions`
# does not depend on string keys.
PROFILE_RANK: dict[ProfileRole, int] = {ProfileRole(role): idx for idx, role in enumerate(PROFILE_LADDER)}


# ---------------------------------------------------------------------------
# UserPermissions value object (REQ-1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserPermissions:
    """Frozen snapshot of "what may this caller do" for a single request.

    SPEC-PORTAL-RBAC-REFACTOR-001 lists 12 fields. We carry one extra
    (``provisioning_status``) so ``get_caller`` can enforce the
    SPEC-INFRA-TENANT-DELETE-001 R1 ``tenant_deleting`` block without
    issuing a second query — preserving AC-1 (single SELECT for resolver
    AND the gate that wraps it).
    """

    user_id: str
    org_id: int
    org_slug: str
    role: ProfileRole
    plan: str
    platform_unlocked_features: frozenset[str]
    effective_role: ProfileRole
    effective_capabilities: frozenset[Capability]
    effective_products: frozenset[str]
    effective_kb_limits: KBLimits
    is_platform_admin: bool
    # Carried for the deprovisioning-block gate in get_caller. Not part of the
    # 12-field public contract; consumers should not branch on it directly.
    provisioning_status: str = "active"


# ---------------------------------------------------------------------------
# Resolver (REQ-1, AC-1)
# ---------------------------------------------------------------------------


def _derive_effective_capabilities(role: ProfileRole, plan: str) -> frozenset[Capability]:
    """Profile capabilities ∩ plan capabilities, with admin bypass.

    Mirrors ``app/api/dependencies.py::get_effective_capabilities``:
    admins on any plan tier receive the full ``knowledge``-tier capability
    set so they can preview what a plan upgrade unlocks before paying for
    it. Intentional per SPEC-PORTAL-PLAN-RENAME-001 (carrying forward the
    SPEC-PORTAL-PROFILES-001 v0.2.0 / v0.3.0 admin-bypass policy).
    """
    if role == ProfileRole.ADMIN:
        # Use the knowledge-tier (full unlock) plan capabilities verbatim —
        # these are the capability strings, but already typed via
        # Capability(StrEnum) so the frozenset equality matches.
        knowledge_caps = get_plan_limits("knowledge").capabilities
        return frozenset(Capability(c) for c in knowledge_caps)

    role_caps = PROFILE_CAPABILITIES.get(role.value, frozenset())
    plan_caps = get_plan_limits(plan).capabilities
    return frozenset(Capability(c) for c in (set(role_caps) & set(plan_caps)))


def _platform_unlocked_set(org: PortalOrg) -> frozenset[str]:
    """Read ``platform_unlocked_features`` defensively.

    SPEC Phase 5 adds the column via Alembic; until that ships, ``getattr``
    returns the model attribute when present (post-Phase-5) or an empty
    list/None (pre-Phase-5). Either way the resolver returns a frozenset.
    """
    raw = getattr(org, "platform_unlocked_features", None) or []
    return frozenset(raw)


# @MX:ANCHOR fan_in=3+ — single source of truth for caller permissions.
# @MX:REASON: every endpoint that switches to declarative gates calls this
# (transitively via `get_caller`); changes here ripple across the portal.
# @MX:SPEC SPEC-PORTAL-RBAC-REFACTOR-001 REQ-1
async def resolve_user_permissions(zitadel_user_id: str, db: AsyncSession) -> UserPermissions | None:
    """Single SELECT + pure derivation. Returns None if user not in portal.

    The SELECT joins ``portal_orgs`` and ``portal_users`` on ``org_id`` and
    filters by zitadel sub. The portal_users `tenant_isolation` policy is
    permissive on the unset-GUC branch (Cat-A), so this query is RLS-safe
    BEFORE ``set_tenant`` has been called — exactly the pattern the existing
    ``_get_caller_org`` relies on.
    """
    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(PortalUser.zitadel_user_id == zitadel_user_id)
    )
    row = result.one_or_none()
    if row is None:
        return None
    org, user = row

    role = ProfileRole(user.role)
    plan = org.plan
    platform_features = _platform_unlocked_set(org)
    effective_caps = _derive_effective_capabilities(role, plan)
    effective_prods = frozenset(derive_user_products(role.value, plan, list(platform_features)))
    kb_limits = effective_kb_limits(role.value, plan)
    is_platform_admin = org.slug == _app_settings.platform_org_slug

    return UserPermissions(
        user_id=zitadel_user_id,
        org_id=org.id,
        org_slug=org.slug,
        role=role,
        plan=plan,
        platform_unlocked_features=platform_features,
        effective_role=role,  # alias-fase: identical to role until profile-stacking lands
        effective_capabilities=effective_caps,
        effective_products=effective_prods,
        effective_kb_limits=kb_limits,
        is_platform_admin=is_platform_admin,
        provisioning_status=org.provisioning_status,
    )


# ---------------------------------------------------------------------------
# Declarative gate dependencies (REQ-1D)
# ---------------------------------------------------------------------------


async def _resolve_caller_with_options(
    credentials: HTTPAuthorizationCredentials,
    db: AsyncSession,
    *,
    allow_during_deprovisioning: bool,
) -> UserPermissions:
    """Shared body for ``get_caller`` and ``get_caller_during_deprovisioning``.

    Both gates do the exact same token-validation + RLS tenant binding +
    structlog binding; they differ only in whether the
    SPEC-INFRA-TENANT-DELETE-001 R1 ``tenant_deleting`` 403 is enforced.
    """
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        logger.warning("get_caller_userinfo_failed", error=str(exc), exc_info=True)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    zitadel_user_id = info.get("sub")
    if not zitadel_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No user found in token")

    perms = await resolve_user_permissions(zitadel_user_id, db)
    if perms is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")

    if not allow_during_deprovisioning and perms.provisioning_status == "deprovisioning":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "tenant_deleting",
                "message": "This organisation is being deleted. No further actions are permitted.",
            },
        )

    await set_tenant(db, perms.org_id)

    # SPEC-INFRA-TENANT-DELETE-001 R6: enable platform-admin RLS on
    # tenant_lifecycle_events when caller is in the platform org. Same value
    # as `admin/__init__::_get_caller_org` — must remain '1' (text), not 'true'.
    if perms.is_platform_admin:
        await db.execute(text("SELECT set_config('app.is_platform_admin', '1', true)"))

    structlog.contextvars.bind_contextvars(org_id=str(perms.org_id), user_id=zitadel_user_id)
    return perms


# @MX:ANCHOR fan_in=8+ — primary FastAPI dependency for portal endpoints.
# Phase 2 sweeps `_get_caller_org` callers to this; treat as stable.
async def get_caller(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserPermissions:
    """Validate the bearer token and return a frozen ``UserPermissions``.

    Mirrors the existing ``admin/__init__::_get_caller_org`` semantics:
    - 401 if the token is invalid
    - 401 if the token has no `sub` claim
    - 404 if the zitadel user has no portal row
    - 403 (``error=tenant_deleting``) if the org is in deprovisioning state
    - sets the RLS tenant GUC for this connection
    - sets ``app.is_platform_admin`` GUC for callers in the platform org
    - binds ``org_id`` + ``user_id`` into structlog contextvars
    """
    return await _resolve_caller_with_options(credentials, db, allow_during_deprovisioning=False)


async def get_caller_during_deprovisioning(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UserPermissions:
    """Variant of ``get_caller`` that does NOT block on deprovisioning state.

    SPEC-INFRA-TENANT-DELETE-001 R1 narrow exception: the
    ``GET /api/admin/orgs/{slug}/deprovision-status`` endpoint must keep
    surfacing progress while the tenant is being deleted, otherwise admins
    can't observe the orchestrator finishing. All other admin actions
    still hit the 403 via ``get_caller``. Use sparingly.
    """
    return await _resolve_caller_with_options(credentials, db, allow_during_deprovisioning=True)


def get_caller_at_least(min_role: ProfileRole):
    """Factory: dependency that requires ``effective_role >= min_role``.

    Drop-in replacement for ``_require_admin(caller_user)`` after the role
    is resolved. Phase 2 sweeps endpoints over to::

        @router.get(...)
        async def endpoint(perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN))):
            ...
    """
    required_rank = PROFILE_RANK[min_role]

    async def _dep(perms: UserPermissions = Depends(get_caller)) -> UserPermissions:
        # Platform-admin bypass: Klai staff (members of the platform org)
        # skip per-tenant role gates. Mirrors the frontend RoleGuard
        # bypass on `user?.isAdmin` so the two surfaces stay consistent.
        # Symptom that triggered adding this: org-owner of the platform
        # org has effective_role='personal' (no per-org admin grant),
        # so taxonomy/coverage and similar admin-gated endpoints would
        # 403 her even though the frontend tab is open.
        if perms.is_platform_admin:
            return perms
        caller_rank = PROFILE_RANK[perms.effective_role]
        if caller_rank < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role {min_role.value!r} or higher required",
            )
        return perms

    return _dep


def require_product(product: str):
    """Factory: dependency that requires the product in
    ``perms.effective_products``."""

    async def _dep(perms: UserPermissions = Depends(get_caller)) -> UserPermissions:
        if product not in perms.effective_products:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Product not available: {product}",
            )
        return perms

    return _dep


def require_capability(capability: Capability):
    """Factory: dependency that requires the capability in
    ``perms.effective_capabilities``."""

    async def _dep(perms: UserPermissions = Depends(get_caller)) -> UserPermissions:
        if capability not in perms.effective_capabilities:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error_code": "capability_required",
                    "capability": capability.value,
                },
            )
        return perms

    return _dep


def require_platform_admin():
    """Factory: dependency that requires the caller to be an ADMIN in the
    platform org (``settings.platform_org_slug``).

    Equivalent of the imperative pair ``_require_admin(caller_user) +
    _require_platform_admin(caller_org)`` that every callsite uses today
    (`retry_provisioning`, `deprovision_org`, future Phase 5 platform-unlock
    endpoints). Both checks must pass: a non-admin user inside the platform
    org gets 403 just like an admin in a regular tenant — the SPEC's intent
    is "Klai staff acting administratively", not "anyone in the Klai
    workspace".
    """

    async def _dep(perms: UserPermissions = Depends(get_caller)) -> UserPermissions:
        if not perms.is_platform_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: platform admin org required",
            )
        if perms.effective_role != ProfileRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: admin role required for platform actions",
            )
        return perms

    return _dep


def require_platform_unlocked(feature: str):
    """Factory: dependency that requires ``feature`` to be in
    ``perms.platform_unlocked_features``.

    Phase 5 of SPEC-PORTAL-RBAC-REFACTOR-001 introduces the
    ``platform_unlocked_features`` column on ``portal_orgs`` via Alembic.
    Until then the column does not exist and this gate effectively 403s
    for every tenant — that is desired behaviour: features behind this
    gate (partner-API, widgets, custom MCPs) stay default-off until Klai
    staff explicitly unlocks them per tenant.
    """

    async def _dep(perms: UserPermissions = Depends(get_caller)) -> UserPermissions:
        if feature not in perms.platform_unlocked_features:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error_code": "feature_not_unlocked", "feature": feature},
            )
        return perms

    return _dep


def assert_platform_unlocked(org: PortalOrg, feature: str) -> None:
    """Imperative check: raise 403 if ``feature`` is not in org's platform_unlocked_features.

    Use this in dependencies that do not go through ``UserPermissions`` (e.g.
    ``get_partner_key`` in ``partner_dependencies.py`` which resolves its own org
    without invoking the OIDC-based ``get_caller`` chain).

    Args:
        org: The resolved PortalOrg ORM instance.
        feature: Feature identifier string (e.g. ``"partner_api"``).

    Raises:
        HTTPException: 403 with ``error_code=feature_not_unlocked`` if not unlocked.
    """
    unlocked = frozenset(getattr(org, "platform_unlocked_features", None) or [])
    if feature not in unlocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "feature_not_unlocked", "feature": feature},
        )


__all__ = [
    "PROFILE_RANK",
    "ProfileRole",
    "UserPermissions",
    "assert_platform_unlocked",
    "get_caller",
    "get_caller_at_least",
    "require_capability",
    "require_platform_admin",
    "require_platform_unlocked",
    "require_product",
    "resolve_user_permissions",
]
