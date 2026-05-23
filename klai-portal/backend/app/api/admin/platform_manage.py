"""Platform-admin cross-tenant WRITE actions — SPEC-PLATFORM-ADMIN-001 fase B+C.

Lets a platform-admin manage users INSIDE any tenant: change role,
suspend/reactivate, and invite/onboard new users. Distinct from
``platform.py`` (read-only) because writes carry a different risk
profile and use a different session strategy.

# @MX:ANCHOR fan_in=4 — cross-tenant write surface
# @MX:REASON: Each endpoint mutates another tenant's data. Gate is
#             require_platform_admin(); writes use
#             tenant_scoped_session(target_org) so RLS enforces the
#             write lands in exactly one tenant (belt + braces on top
#             of the platform-admin gate). Every action is audited.
# @MX:SPEC: SPEC-PLATFORM-ADMIN-001

Session strategy: NOT cross_org_session (RLS bypass). Instead
``tenant_scoped_session(target_org_id)`` sets app.current_org_id to
the target org, so Category-D RLS (portal_knowledge_bases on
personal-KB creation, etc.) passes for that org and the write is
provably scoped. The platform-admin gate is what authorises picking
an arbitrary target_org_id.
"""

from __future__ import annotations

import re
import unicodedata
from contextlib import suppress
from typing import Literal
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, set_tenant, tenant_scoped_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.core.seats import suggest_seat
from app.models.portal import PortalOrg, PortalUser
from app.services.audit import log_event
from app.services.auth_links import AuthLinkRoute, build_url_template
from app.services.default_knowledge_bases import create_default_personal_kb
from app.services.provisioning import provision_tenant
from app.services.zitadel import zitadel


def _slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9\s-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:60] if name else "org"


def _to_slug(name: str, suffix: str = "") -> str:
    base = re.sub(r"[^a-z0-9]+", "-", _slugify(name).lower()).strip("-") or "org"
    if suffix:
        base = f"{base}-{suffix[:8]}"
    return base[:64]


logger = structlog.get_logger()

router = APIRouter(prefix="/platform", tags=["platform-admin"])

PortalRole = Literal["personal", "company", "kb_manager", "group_manager", "admin"]

# Mirror of users.py::_ZITADEL_ROLE_BY_PORTAL_ROLE — only admin gets a
# Zitadel project grant; other roles rely on portal_users.role.
_ZITADEL_ROLE_BY_PORTAL_ROLE: dict[str, str | None] = {
    "personal": None,
    "company": None,
    "kb_manager": None,
    "group_manager": None,
    "admin": "org:owner",
}


class MessageResponse(BaseModel):
    message: str


class RoleUpdateRequest(BaseModel):
    role: PortalRole


class PlatformInviteRequest(BaseModel):
    email: str
    first_name: str = Field(min_length=1)
    last_name: str = Field(min_length=1)
    role: PortalRole = "personal"
    preferred_language: Literal["nl", "en"] = "nl"


async def _load_org_or_404(org_id: int) -> PortalOrg:
    async with tenant_scoped_session(org_id) as db:
        org = (
            await db.execute(select(PortalOrg).where(PortalOrg.id == org_id, PortalOrg.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
        # Detach while every column is still loaded so callers can read
        # attributes (e.g. org.slug) after the session closes. Without this the
        # instance is detached on session exit and the first attribute access
        # triggers a refresh -> DetachedInstanceError (500 on platform invite).
        db.expunge(org)
    return org


# ---------------------------------------------------------------------------
# Role change
# ---------------------------------------------------------------------------


@router.patch(
    "/organizations/{org_id}/users/{zitadel_user_id}/role",
    response_model=MessageResponse,
)
async def platform_update_role(
    org_id: int,
    zitadel_user_id: str,
    body: RoleUpdateRequest,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> MessageResponse:
    """Change a user's role inside a target tenant. Refuses to demote the
    last admin (mirrors the per-tenant invariant)."""
    async with tenant_scoped_session(org_id) as db:
        # Lock the org row for serialisable role-change semantics.
        locked = (
            await db.execute(
                select(PortalOrg).where(PortalOrg.id == org_id, PortalOrg.deleted_at.is_(None)).with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise HTTPException(status_code=404, detail="Organisatie niet gevonden")

        user = (
            await db.execute(
                select(PortalUser).where(
                    PortalUser.zitadel_user_id == zitadel_user_id,
                    PortalUser.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

        if user.role == "admin" and body.role != "admin":
            admin_count = await db.scalar(
                select(func.count())
                .select_from(PortalUser)
                .where(PortalUser.org_id == org_id, PortalUser.role == "admin")
            )
            if (admin_count or 0) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="Kan rol niet wijzigen: dit is de laatste admin.",
                )

        user.role = body.role
        await db.commit()

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_role_changed",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={"target_org_id": org_id, "new_role": body.role},
    )
    return MessageResponse(message="Rol bijgewerkt.")


# ---------------------------------------------------------------------------
# Suspend / reactivate
# ---------------------------------------------------------------------------


@router.post(
    "/organizations/{org_id}/users/{zitadel_user_id}/suspend",
    response_model=MessageResponse,
)
async def platform_suspend(
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> MessageResponse:
    async with tenant_scoped_session(org_id) as db:
        user = (
            await db.execute(
                select(PortalUser).where(
                    PortalUser.zitadel_user_id == zitadel_user_id,
                    PortalUser.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
        if user.status in ("suspended", "offboarded"):
            raise HTTPException(
                status_code=409,
                detail=f"Gebruiker heeft status '{user.status}'.",
            )
        user.status = "suspended"
        await db.commit()

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_suspended",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={"target_org_id": org_id},
    )
    return MessageResponse(message="Gebruiker gesuspendeerd.")


@router.post(
    "/organizations/{org_id}/users/{zitadel_user_id}/reactivate",
    response_model=MessageResponse,
)
async def platform_reactivate(
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> MessageResponse:
    async with tenant_scoped_session(org_id) as db:
        user = (
            await db.execute(
                select(PortalUser).where(
                    PortalUser.zitadel_user_id == zitadel_user_id,
                    PortalUser.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
        if user.status != "suspended":
            raise HTTPException(
                status_code=409,
                detail=f"Gebruiker heeft status '{user.status}'.",
            )
        user.status = "active"
        await db.commit()

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_reactivated",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={"target_org_id": org_id},
    )
    return MessageResponse(message="Gebruiker geheractiveerd.")


# ---------------------------------------------------------------------------
# Hard-delete a user (everything)
# ---------------------------------------------------------------------------


@router.delete(
    "/organizations/{org_id}/users/{zitadel_user_id}",
    response_model=MessageResponse,
)
async def platform_delete_user(
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> MessageResponse:
    """Hard-delete a user from a tenant — everything that is theirs.

    Purges their KBs (personal + solely-owned org KBs), revokes partner API
    keys + MCP tokens, deletes the Zitadel identity (frees the email), and
    removes the ``portal_users`` row. Irreversible. Platform-admin only.

    Deleting the sole owner of a tenant leaves an empty org — use the
    tenant-deprovision endpoint to remove the whole tenant instead.
    """
    from app.core.config import settings  # local import avoids cycle
    from app.services.kb_offboarding import (
        KbDisposition,
        apply_dispositions,
        compute_offboard_preview,
        revoke_user_credentials,
    )

    async with tenant_scoped_session(org_id) as db:
        org = (await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))).scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=404, detail="Organisatie niet gevonden")
        user = (
            await db.execute(
                select(PortalUser).where(
                    PortalUser.zitadel_user_id == zitadel_user_id,
                    PortalUser.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")

        # 1. Purge KBs: personal + solely-owned org KBs (complete delete).
        preview = await compute_offboard_preview(zitadel_user_id, org_id, db)
        dispositions = [
            KbDisposition(kb_id=kb.kb_id, action="delete")
            for kb in (*preview.personal_kbs, *preview.org_kbs_solely_owned)
        ]
        if dispositions:
            await apply_dispositions(zitadel_user_id, dispositions, perms.user_id, org, db)

        # 2. Revoke partner API keys + MCP tokens.
        api_keys, mcp_tokens = await revoke_user_credentials(zitadel_user_id, org_id, db)

        # 3. Delete the Zitadel identity (frees the email). External call first:
        #    a failure here aborts before any DB commit (session rolls back).
        try:
            await zitadel.remove_user(
                org_id=settings.zitadel_portal_org_id,
                zitadel_user_id=zitadel_user_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Zitadel-verwijdering mislukt: {exc}") from exc

        # 4. Delete the portal_users row (cascades memberships/capabilities).
        await db.delete(user)
        await db.commit()

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_deleted",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={
            "target_org_id": org_id,
            "kbs_deleted": len(dispositions),
            "api_keys_revoked": api_keys,
            "mcp_tokens_revoked": mcp_tokens,
        },
    )
    return MessageResponse(message="Gebruiker volledig verwijderd.")


# ---------------------------------------------------------------------------
# Invite / onboard into a target tenant
# ---------------------------------------------------------------------------


class PlatformInviteResponse(BaseModel):
    user_id: str
    message: str


@router.post(
    "/organizations/{org_id}/users/invite",
    response_model=PlatformInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def platform_invite(
    org_id: int,
    body: PlatformInviteRequest,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> PlatformInviteResponse:
    """Onboard a new user directly into a target tenant.

    Mirrors admin/users.py::invite_user but targets an arbitrary org.
    Zitadel always uses the single portal org; tenancy is set via
    portal_users.org_id. The portal_user INSERT + personal-KB creation
    run inside tenant_scoped_session(org_id) so Category-D RLS passes
    for the target org.
    """
    org = await _load_org_or_404(org_id)
    from app.core.config import settings  # local import avoids cycle

    # 1. Create the Zitadel user (single portal org), no auto-mail.
    try:
        user_data = await zitadel.invite_user(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            preferred_language=body.preferred_language,
        )
    except Exception as exc:
        logger.exception("platform_invite_zitadel_failed", email=body.email)
        raise HTTPException(status_code=502, detail=f"Zitadel invite mislukt: {exc}") from exc
    zitadel_user_id: str = user_data["userId"]

    # 2. Send the activation mail with the Klai url-template.
    invite_url_template = build_url_template(AuthLinkRoute.PASSWORD_SET)
    try:
        await zitadel.send_invite_code(zitadel_user_id, url_template=invite_url_template)
    except Exception as exc:
        logger.exception("platform_invite_mail_failed", zitadel_user_id=zitadel_user_id)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "invite_partial_failure",
                "user_id": zitadel_user_id,
                "message": "User aangemaakt maar invite-mail mislukt.",
            },
        ) from exc

    # 3. Admin-only Zitadel grant.
    zitadel_role = _ZITADEL_ROLE_BY_PORTAL_ROLE.get(body.role)
    if zitadel_role is not None:
        try:
            await zitadel.grant_user_role(
                org_id=settings.zitadel_portal_org_id,
                user_id=zitadel_user_id,
                role=zitadel_role,
            )
        except Exception as exc:
            logger.exception("platform_invite_grant_failed", zitadel_user_id=zitadel_user_id)
            raise HTTPException(status_code=502, detail=f"Rol-grant mislukt: {exc}") from exc

    # 4. portal_user row + personal KB in the TARGET tenant context.
    async with tenant_scoped_session(org_id) as db:
        user_row = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_id,
            role=body.role,
            seat_type=str(suggest_seat(body.role)),
            preferred_language=body.preferred_language,
        )
        db.add(user_row)
        await create_default_personal_kb(zitadel_user_id, org_id, db)
        await db.commit()

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_invited",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={
            "target_org_id": org_id,
            "target_org_slug": org.slug,
            "role": body.role,
            "url_template_host": urlparse(invite_url_template).netloc,
        },
    )
    return PlatformInviteResponse(
        user_id=zitadel_user_id,
        message=f"Uitnodiging verstuurd naar {body.email}.",
    )


# ---------------------------------------------------------------------------
# Create a brand-new tenant + owner
# ---------------------------------------------------------------------------


class CreateTenantRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=128)
    owner_email: str
    owner_first_name: str = Field(min_length=1)
    owner_last_name: str = Field(min_length=1)
    preferred_language: Literal["nl", "en"] = "nl"


class CreateTenantResponse(BaseModel):
    org_id: int
    slug: str
    owner_user_id: str
    message: str


@router.post(
    "/organizations",
    response_model=CreateTenantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def platform_create_tenant(
    body: CreateTenantRequest,
    background_tasks: BackgroundTasks,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> CreateTenantResponse:
    """Create a new tenant + its first admin (owner) from the platform
    console. Mirrors the public signup flow but: (a) gated on
    platform-admin, (b) the owner is invited (no password — gets an
    activation mail), (c) provisioning is queued as a background task.
    """
    # 1. Zitadel org.
    try:
        org_data = await zitadel.create_org(_slugify(body.company_name))
    except Exception as exc:
        logger.exception("platform_create_tenant_org_failed", name=body.company_name)
        raise HTTPException(status_code=502, detail=f"Org-creatie mislukt: {exc}") from exc
    zitadel_org_id: str = org_data["id"]

    # Orphan-prevention: every failure AFTER create_org must cascade-delete
    # the Zitadel org, else a half-built tenant leaks (and retry with the same
    # email/company 409s on "already exists"). delete_org is idempotent and
    # cascades users + grants.
    async def _rollback_zitadel_org() -> None:
        with suppress(Exception):
            await zitadel.delete_org(zitadel_org_id)

    # 2. Owner user via invite (no password; activation mail).
    try:
        user_data = await zitadel.invite_user(
            org_id=settings_zitadel_portal_org_id(),
            email=body.owner_email,
            first_name=body.owner_first_name,
            last_name=body.owner_last_name,
            preferred_language=body.preferred_language,
        )
    except Exception as exc:
        logger.exception("platform_create_tenant_owner_failed", email=body.owner_email)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Owner-creatie mislukt: {exc}") from exc
    owner_user_id: str = user_data["userId"]

    invite_url_template = build_url_template(AuthLinkRoute.PASSWORD_SET)
    try:
        await zitadel.send_invite_code(owner_user_id, url_template=invite_url_template)
        await zitadel.grant_user_role(
            org_id=settings_zitadel_portal_org_id(),
            user_id=owner_user_id,
            role="org:owner",
        )
    except Exception as exc:
        logger.exception("platform_create_tenant_owner_setup_failed", email=body.owner_email)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Owner-setup mislukt: {exc}") from exc

    # 3. PortalOrg + owner PortalUser. Org insert needs no tenant; the
    # user insert needs set_tenant for the portal_users RLS check.
    owner_email_domain = body.owner_email.split("@")[-1].strip().lower()
    org_row = PortalOrg(
        zitadel_org_id=zitadel_org_id,
        name=body.company_name,
        slug=_to_slug(body.company_name, zitadel_org_id),
        primary_domain=owner_email_domain,
        auto_accept_same_domain=False,
    )
    try:
        db.add(org_row)
        await db.flush()
        await set_tenant(db, org_row.id)
        db.add(
            PortalUser(
                zitadel_user_id=owner_user_id,
                org_id=org_row.id,
                role="admin",
                seat_type=str(suggest_seat("admin")),
                preferred_language=body.preferred_language,
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("platform_create_tenant_db_failed", email=body.owner_email)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Opslaan mislukt: {exc}") from exc

    # 4. Cache + provisioning. Local import avoids an auth.py import cycle.
    from app.api.auth import invalidate_tenant_slug_cache

    invalidate_tenant_slug_cache()
    background_tasks.add_task(provision_tenant, org_row.id)

    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.tenant_created",
        resource_type="organization",
        resource_id=str(org_row.id),
        details={
            "slug": org_row.slug,
            "company_name": body.company_name,
            "owner_email": body.owner_email,
        },
    )
    return CreateTenantResponse(
        org_id=org_row.id,
        slug=org_row.slug,
        owner_user_id=owner_user_id,
        message=f"Tenant '{body.company_name}' aangemaakt. Provisioning gestart.",
    )


def settings_zitadel_portal_org_id() -> str:
    """Lazy settings access (avoids a top-level config import cycle)."""
    from app.core.config import settings

    return settings.zitadel_portal_org_id


__all__ = ["router"]
