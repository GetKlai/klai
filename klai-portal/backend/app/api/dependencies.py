"""Shared FastAPI dependencies."""

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user_id
from app.api.bearer import bearer as bearer  # re-export for routes that import from here
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.core.plan_limits import PLAN_LIMITS, get_plan_limits
from app.core.profiles import (
    PROFILE_CAPABILITIES,
    Capability,
)
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.services.access import is_personal_kb
from app.services.entitlements import get_effective_products


async def get_kb_with_access(
    kb_slug: str,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> PortalKnowledgeBase:
    """Resolve a KB by slug + enforce the personal-firewall.

    SPEC-PORTAL-KB-OWNERSHIP-001 REQ-3.1 — single source of truth for
    every ``/api/app/knowledge-bases/{kb_slug}/...`` route.

    Three steps, in order:

    1. **Magic-slug shortcuts**: ``personal`` resolves to the caller's
       ``personal-{user_id}`` KB; ``org`` resolves to the tenant's org
       KB. Both are lazy-created if provisioning missed them. These
       slugs are by definition owned-by-or-visible-to the caller, so
       the firewall step is a no-op.
    2. **Tenant-scope**: SELECT WHERE org_id = caller.org_id AND slug = kb_slug.
       Cross-tenant slugs return 0 rows -> 404. Org-scoping is also
       enforced at the DB level via Cat-D RLS on
       ``portal_knowledge_bases``; this is belt+braces.
    3. **Personal-firewall**: if the resolved KB is personal
       (``is_personal_kb()``) AND the caller is not the
       ``owner_user_id``, raise 404. NOT 403 — leaking existence of a
       personal KB to non-owners is itself a privacy violation. Admins
       also receive 404 (no role-bypass).

    Authorisation (owner / contributor / viewer) is layered on TOP of
    this gate via ``_require_owner`` etc. in the KB API module. This
    dependency only handles existence + privacy; it does not gate
    write actions.
    """
    # Magic-slug shortcuts. Imported here to avoid a top-level circular import
    # via app.services.default_knowledge_bases -> set_tenant -> ... -> dependencies.
    if kb_slug == "personal":
        from app.services.default_knowledge_bases import resolve_personal_kb

        return await resolve_personal_kb(perms.user_id, perms.org_id, db)
    if kb_slug == "org":
        from app.services.default_knowledge_bases import resolve_org_kb

        return await resolve_org_kb(perms.user_id, perms.org_id, db)

    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == perms.org_id,
            PortalKnowledgeBase.slug == kb_slug,
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    # Personal-firewall: existence-non-disclosure.
    if is_personal_kb(kb) and kb.owner_user_id != perms.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return kb


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


async def _load_org_or_500(db: AsyncSession, org_id: int) -> PortalOrg:
    """Load the full ``PortalOrg`` row for an org_id from ``perms.org_id``.

    Endpoints that take ``perms: UserPermissions = Depends(get_caller)`` only
    receive the org_id, but a handful need the full ORM row for fields not
    on ``UserPermissions`` (``zitadel_org_id``, ``moneybird_*``,
    ``librechat_container``, etc.). This helper centralises the load-and-
    raise-500-if-missing pattern so per-file copies stay in sync.

    The 500 is intentional: ``perms`` was just resolved by ``get_caller``,
    which already implies the org row exists. A miss here means the row
    disappeared mid-request, which is a server-side invariant violation,
    not a client-side 404.
    """
    org = await db.get(PortalOrg, org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Organisation not found",
        )
    return org


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

    # @MX:NOTE -- Admin-bypass: an admin role on any plan tier (including "chat")
    # receives the full "knowledge"-tier capability set. This is INTENTIONAL per
    # SPEC-PORTAL-PLAN-RENAME-001 (carrying forward the SPEC-PORTAL-PROFILES-001
    # v0.2.0 / v0.3.0 admin-bypass policy): an admin must be able to preview /
    # test what a plan upgrade unlocks BEFORE the org commits to the higher
    # billing tier. Without this, a "chat"-plan admin who wants to evaluate
    # the connector ecosystem before paying for "knowledge" is blocked.
    #
    # Trade-off: this gives admins more capabilities than their plan technically
    # pays for. Acceptable because (a) admins are the billing-decision-maker
    # anyway, (b) the per-user capabilities of NON-admin users on the same org
    # are still constrained by the plan ceiling (so a "chat"-tenant's regular
    # users still hit the limits).
    #
    # To remove the bypass: replace the "if user.role == 'admin'" branch with
    # the same intersection logic used for other roles. Tests that assert
    # "test_admin_on_chat_gets_knowledge_tier" would need updating.
    if user.role == "admin":
        return set(PLAN_LIMITS["knowledge"].capabilities)

    # All other roles: intersect role capabilities with plan capabilities.
    role_caps = PROFILE_CAPABILITIES.get(user.role, frozenset())
    plan_caps = get_plan_limits(org.plan).capabilities
    return set(role_caps) & set(plan_caps)
