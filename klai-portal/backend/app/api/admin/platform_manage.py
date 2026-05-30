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

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import cross_org_session, get_db, tenant_scoped_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.core.seats import suggest_seat
from app.models.portal import PortalOrg, PortalUser
from app.services.audit import log_event
from app.services.auth_links import AuthLinkRoute, build_url_template
from app.services.default_knowledge_bases import create_default_personal_kb
from app.services.domain_validation import primary_domain_for_email_domain
from app.services.mcp_role_notifier import fire_role_change_notification
from app.services.provisioning import provision_tenant
from app.services.user_deletion_orchestrator import delete_user_with_state_machine
from app.services.user_memberships import get_user_global_membership_state, get_user_membership_summary
from app.services.zitadel import _sync_zitadel_role_grant, zitadel


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


async def _emit_audit_safe(action: str, details: dict, perms: UserPermissions) -> None:
    """Emit an audit event; fall back to structlog on DB failure (REQ-6 AC6.3).

    # @MX:NOTE: [AUTO] Used for partial-failure audit paths where the primary session
    # may be aborted. Mirrors the fallback pattern in kb_offboarding._do_delete.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-6
    """
    try:
        await log_event(
            org_id=perms.org_id,
            actor=perms.user_id,
            action=action,
            resource_type="user",
            resource_id="",
            details=details,
        )
    except Exception:
        logger.exception(
            "platform_admin_audit_emit_failed",
            original_action=action,
            original_details=details,
        )


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


class RoleUpdateResponse(MessageResponse):
    """Response for role-change endpoints; carries zitadel_sync_failed flag (REQ-5)."""

    zitadel_sync_failed: bool = False


class SuspendResponse(MessageResponse):
    """Response for suspend/reactivate endpoints; carries zitadel_sync_failed
    flag (REQ-12) so the admin UI can warn when the Zitadel lock/unlock
    out-of-sync state needs manual recovery."""

    zitadel_sync_failed: bool = False


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


async def _rollback_zitadel_user(zitadel_user_id: str) -> None:
    with suppress(Exception):
        await zitadel.remove_user(
            org_id=settings_zitadel_portal_org_id(),
            zitadel_user_id=zitadel_user_id,
        )


# ---------------------------------------------------------------------------
# Role change
# ---------------------------------------------------------------------------


@router.patch(
    "/organizations/{org_id}/users/{zitadel_user_id}/role",
    response_model=RoleUpdateResponse,
)
async def platform_update_role(
    org_id: int,
    zitadel_user_id: str,
    body: RoleUpdateRequest,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> RoleUpdateResponse:
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

        old_role = user.role
        user.role = body.role
        await db.commit()

    fire_role_change_notification(zitadel_user_id)
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_role_changed",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={"target_org_id": org_id, "new_role": body.role},
    )

    # REQ-5 (Finding A-4): sync Zitadel org:owner grant after DB commit.
    # @MX:NOTE: [AUTO] Failure is non-fatal: DB is already committed; we emit
    # a desync audit event and surface zitadel_sync_failed in the response.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-5
    zitadel_sync_failed = False
    try:
        await _sync_zitadel_role_grant(zitadel_user_id, old_role=old_role, new_role=body.role)
    except Exception:
        zitadel_sync_failed = True
        logger.exception("platform_role_change_zitadel_sync_failed", zitadel_user_id=zitadel_user_id)
        await _emit_audit_safe(
            action="platform_admin.role_change_zitadel_desync",
            details={
                "db_role": body.role,
                "target_zitadel_role": "org:owner",
                "zitadel_sync_failed": True,
                "target_org_id": org_id,
            },
            perms=perms,
        )

    return RoleUpdateResponse(message="Rol bijgewerkt.", zitadel_sync_failed=zitadel_sync_failed)


# ---------------------------------------------------------------------------
# Suspend / reactivate
# ---------------------------------------------------------------------------


@router.post(
    "/organizations/{org_id}/users/{zitadel_user_id}/suspend",
    response_model=SuspendResponse,
)
async def platform_suspend(
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> SuspendResponse:
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

    # REQ-12 (Finding A-6): lock the Zitadel identity AFTER the DB commit so
    # the desync-window favours "DB committed, Zitadel still active" (caller
    # remains logged in until token expiry) over "DB rolled back, Zitadel
    # locked" (caller mysteriously locked out with no DB trace).
    # @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-12
    zitadel_sync_failed = False
    try:
        membership_state = await get_user_global_membership_state(zitadel_user_id)
        if membership_state.active_count == 0:
            await zitadel.lock_user(
                zitadel_user_id=zitadel_user_id,
                org_id=settings_zitadel_portal_org_id(),
            )
    except Exception as exc:
        logger.exception("platform_suspend_zitadel_lock_failed", zitadel_user_id=zitadel_user_id)
        await _emit_audit_safe(
            action="platform_admin.suspend_zitadel_desync",
            details={
                "target_zitadel_user_id": zitadel_user_id,
                "target_org_id": org_id,
                "error": str(exc)[:200],
            },
            perms=perms,
        )
        zitadel_sync_failed = True

    fire_role_change_notification(zitadel_user_id)
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_suspended",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={"target_org_id": org_id, "zitadel_sync_failed": zitadel_sync_failed},
    )
    return SuspendResponse(
        message="Gebruiker gesuspendeerd.",
        zitadel_sync_failed=zitadel_sync_failed,
    )


@router.post(
    "/organizations/{org_id}/users/{zitadel_user_id}/reactivate",
    response_model=SuspendResponse,
)
async def platform_reactivate(
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> SuspendResponse:
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

    # REQ-12 (Finding A-6): unlock Zitadel after DB commit (same ordering rationale
    # as platform_suspend — DB is source-of-truth, Zitadel mirrors).
    # @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-12
    zitadel_sync_failed = False
    try:
        membership_state = await get_user_global_membership_state(zitadel_user_id)
        if membership_state.active_count > 0:
            await zitadel.unlock_user(
                zitadel_user_id=zitadel_user_id,
                org_id=settings_zitadel_portal_org_id(),
            )
    except Exception as exc:
        logger.exception("platform_reactivate_zitadel_unlock_failed", zitadel_user_id=zitadel_user_id)
        await _emit_audit_safe(
            action="platform_admin.reactivate_zitadel_desync",
            details={
                "target_zitadel_user_id": zitadel_user_id,
                "target_org_id": org_id,
                "error": str(exc)[:200],
            },
            perms=perms,
        )
        zitadel_sync_failed = True

    fire_role_change_notification(zitadel_user_id)
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="platform_admin.user_reactivated",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={"target_org_id": org_id, "zitadel_sync_failed": zitadel_sync_failed},
    )
    return SuspendResponse(
        message="Gebruiker geheractiveerd.",
        zitadel_sync_failed=zitadel_sync_failed,
    )


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

    Uses the user_deletion_orchestrator state machine (REQ-4) so that partial
    failures are recorded on portal_users.deletion_status and can be retried.
    """
    return await _execute_user_delete(
        org_id=org_id,
        zitadel_user_id=zitadel_user_id,
        perms=perms,
    )


@router.post(
    "/organizations/{org_id}/users/{zitadel_user_id}/retry-delete",
    response_model=MessageResponse,
)
async def platform_retry_user_delete(
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
) -> MessageResponse:
    """Restart the user-delete state machine from scratch.

    Each step is idempotent — already-deleted resources are skipped
    harmlessly. Use this after a portal_users.deletion_status='failed_partial'
    to complete the deletion.
    """
    return await _execute_user_delete(
        org_id=org_id,
        zitadel_user_id=zitadel_user_id,
        perms=perms,
    )


async def _execute_user_delete(
    *,
    org_id: int,
    zitadel_user_id: str,
    perms: UserPermissions,
) -> MessageResponse:
    """Shared implementation for platform_delete_user and platform_retry_user_delete.

    Resolves all pre-conditions, then delegates to delete_user_with_state_machine.
    The orchestrator records partial failures on portal_users so the retry
    endpoint can restart from scratch.
    """
    from app.services.kb_offboarding import (
        KbDisposition,
        compute_offboard_preview,
        revoke_user_credentials,
    )

    if zitadel_user_id == perms.user_id:
        raise HTTPException(status_code=409, detail="Platform-admin kan zichzelf niet verwijderen.")

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

        membership_summary = await get_user_membership_summary(
            zitadel_user_id,
            excluding_org_id=org_id,
        )
        if membership_summary.is_platform_admin:
            raise HTTPException(
                status_code=409,
                detail="Platform-admin identities kunnen niet via tenant-delete worden verwijderd.",
            )
        delete_global_identity = membership_summary.remaining_count == 0

        # Pre-compute KB dispositions + revoke credentials before entering the
        # state machine. Credential revocation is safe to do here because it is
        # idempotent (already-revoked keys are skipped).
        preview = await compute_offboard_preview(zitadel_user_id, org_id, db)
        kb_dispositions = [
            KbDisposition(kb_id=kb.kb_id, action="delete")
            for kb in (*preview.personal_kbs, *preview.org_kbs_solely_owned)
        ]
        api_keys, mcp_tokens = await revoke_user_credentials(zitadel_user_id, org_id, db)

        # Delegate to the state machine. It records partial failures and emits
        # the audit event. We pass the open tenant-scoped session so step 3
        # (portal_db_delete) runs in the same RLS context.
        success = await delete_user_with_state_machine(
            org_id=org_id,
            zitadel_user_id=zitadel_user_id,
            actor_user_id=perms.user_id,
            delete_global_identity=delete_global_identity,
            kb_dispositions=kb_dispositions,
            api_keys_count=api_keys,
            mcp_tokens_count=mcp_tokens,
            org=org,
            portal_user=user,
            db=db,
        )

        if success:
            await db.commit()

    fire_role_change_notification(zitadel_user_id)

    if not success:
        # Partial failure — orchestrator already wrote deletion_status and audit.
        raise HTTPException(
            status_code=502,
            detail=("Verwijdering gedeeltelijk mislukt. Gebruik POST .../retry-delete om opnieuw te proberen."),
        )

    message = "Gebruiker volledig verwijderd." if delete_global_identity else "Gebruiker uit tenant verwijderd."
    return MessageResponse(message=message)


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
        # REQ-6 (Finding A-7): emit audit event for permanent trail even though
        # VictoriaLogs captures the exception above (30-day retention only).
        await _emit_audit_safe(
            action="platform_admin.invite_zitadel_invite_failed",
            details={
                "target_email": body.email,
                "target_org_id": org_id,
                "error": str(exc)[:200],
            },
            perms=perms,
        )
        raise HTTPException(status_code=502, detail=f"Zitadel invite mislukt: {exc}") from exc
    zitadel_user_id: str = user_data["userId"]

    # 2. Admin-only Zitadel grant.
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
            await _emit_audit_safe(
                action="platform_admin.invite_grant_role_failed",
                details={
                    "target_email": body.email,
                    "target_org_id": org_id,
                    "error": str(exc)[:200],
                },
                perms=perms,
            )
            await _rollback_zitadel_user(zitadel_user_id)
            raise HTTPException(status_code=502, detail=f"Rol-grant mislukt: {exc}") from exc

    # 3. portal_user row + personal KB in the TARGET tenant context.
    try:
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
    except Exception:
        logger.exception("platform_invite_db_failed", zitadel_user_id=zitadel_user_id)
        await _rollback_zitadel_user(zitadel_user_id)
        raise

    # 4. Send the activation mail after the portal row exists. If mail fails,
    # keep the valid portal account so support can retry delivery.
    invite_url_template = build_url_template(AuthLinkRoute.PASSWORD_SET)
    mail_sent = True
    try:
        await zitadel.send_invite_code(zitadel_user_id, url_template=invite_url_template)
    except Exception:
        logger.exception("platform_invite_mail_failed", zitadel_user_id=zitadel_user_id)
        mail_sent = False

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
            "invite_mail_sent": mail_sent,
        },
    )
    message = (
        f"Uitnodiging verstuurd naar {body.email}."
        if mail_sent
        else f"User aangemaakt voor {body.email}, maar invite-mail kon niet worden verstuurd."
    )

    from app.services.listmonk import sync_portal_user_best_effort

    await sync_portal_user_best_effort(
        email=body.email,
        name=f"{body.first_name} {body.last_name}".strip(),
        company=org.name,
        org_id=org_id,
        portal_user_id=getattr(user_row, "id", None),
        zitadel_user_id=zitadel_user_id,
        source="portal_platform_invite",
    )

    return PlatformInviteResponse(
        user_id=zitadel_user_id,
        message=message,
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


async def _create_or_reuse_tenant_owner_user(body: CreateTenantRequest) -> tuple[str, bool]:
    """Return ``(zitadel_user_id, created)`` for a new-tenant owner."""
    try:
        user_data = await zitadel.invite_user(
            org_id=settings_zitadel_portal_org_id(),
            email=body.owner_email,
            first_name=body.owner_first_name,
            last_name=body.owner_last_name,
            preferred_language=body.preferred_language,
        )
        return user_data["userId"], True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise

        # The owner may already exist in Zitadel after an earlier partial
        # attempt or because they signed up socially first. Reuse that identity
        # instead of treating the tenant create as non-recoverable.
        existing_user_id = await zitadel.find_user_id_by_email(body.owner_email)
        if not existing_user_id:
            logger.exception("platform_create_tenant_owner_conflict_unresolved", email=body.owner_email)
            raise
        logger.warning(
            "platform_create_tenant_owner_exists_reusing_user",
            email=body.owner_email,
            zitadel_user_id=existing_user_id,
        )
        return existing_user_id, False


async def _grant_tenant_owner_role(owner_user_id: str, owner_email: str) -> None:
    try:
        await zitadel.grant_user_role(
            org_id=settings_zitadel_portal_org_id(),
            user_id=owner_user_id,
            role="org:owner",
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            logger.warning("platform_create_tenant_owner_role_exists", email=owner_email)
            return
        raise


async def _rollback_tenant_owner_identity(owner_user_id: str, owner_user_created: bool) -> None:
    if owner_user_created:
        await _rollback_zitadel_user(owner_user_id)
        return

    with suppress(Exception):
        await _sync_zitadel_role_grant(
            zitadel_user_id=owner_user_id,
            old_role="company",
            new_role="company",
        )


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

    # Orphan-prevention: every failure AFTER create_org must delete both the
    # new tenant org and, once created below, the owner user in the central
    # portal org.
    async def _rollback_zitadel_org() -> None:
        with suppress(Exception):
            await zitadel.delete_org(zitadel_org_id)

    try:
        owner_user_id, owner_user_created = await _create_or_reuse_tenant_owner_user(body)
    except Exception as exc:
        logger.exception("platform_create_tenant_owner_failed", email=body.owner_email)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Owner-creatie mislukt: {exc}") from exc

    try:
        await _grant_tenant_owner_role(owner_user_id, body.owner_email)
    except Exception as exc:
        logger.exception("platform_create_tenant_owner_setup_failed", email=body.owner_email)
        # REQ-6 (Finding A-7): permanent audit trail for grant failure.
        await _emit_audit_safe(
            action="platform_admin.create_tenant_grant_role_failed",
            details={
                "target_email": body.owner_email,
                "target_org_id": None,
                "target_zitadel_org_id": zitadel_org_id,
                "error": str(exc)[:200],
            },
            perms=perms,
        )
        await _rollback_tenant_owner_identity(owner_user_id, owner_user_created)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Owner-setup mislukt: {exc}") from exc

    # 3a. PortalOrg insert on the request-scoped session. portal_orgs is
    # portal_api-owned (no per-tenant RLS), so no tenant context needed.
    # Commit immediately so the org_id is durable + visible to the
    # tenant-scoped session that follows.
    owner_email_domain = body.owner_email.split("@")[-1].strip().lower()
    org_row = PortalOrg(
        zitadel_org_id=zitadel_org_id,
        name=body.company_name,
        slug=_to_slug(body.company_name, zitadel_org_id),
        plan="knowledge",
        primary_domain=primary_domain_for_email_domain(owner_email_domain),
        auto_accept_same_domain=False,
    )
    try:
        db.add(org_row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.exception("platform_create_tenant_org_db_failed", email=body.owner_email)
        await _rollback_tenant_owner_identity(owner_user_id, owner_user_created)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Opslaan org mislukt: {exc}") from exc

    # 3b. Owner PortalUser insert in a SEPARATE tenant_scoped_session per REQ-10
    # (Finding A-3, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001). The request-scoped
    # session is NEVER mutated with set_tenant — this matches standards.md § 3
    # and mirrors platform_invite.
    # @MX:NOTE: REQ-10 — tenant_scoped_session opens a fresh AsyncSession +
    # sets app.current_org_id; the request session stays at NULL GUC so a
    # future read between this block and handler-return cannot accidentally
    # land on the wrong tenant.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-10 (Finding A-3)
    owner_user_row: PortalUser | None = None
    try:
        async with tenant_scoped_session(org_row.id) as tdb:
            owner_user_row = PortalUser(
                zitadel_user_id=owner_user_id,
                org_id=org_row.id,
                role="admin",
                seat_type=str(suggest_seat("admin")),
                preferred_language=body.preferred_language,
            )
            tdb.add(owner_user_row)
            await tdb.commit()
    except Exception as exc:
        logger.exception("platform_create_tenant_owner_user_db_failed", email=body.owner_email)
        # Org row was committed above; remove it via cross_org_session so the
        # tenant doesn't survive as an owner-less shell. portal_orgs has no
        # FK rows yet (provisioning has not started), so a flat DELETE is safe.
        try:
            async with cross_org_session() as cdb:
                await cdb.execute(
                    text("DELETE FROM portal_orgs WHERE id = :id"),
                    {"id": org_row.id},
                )
                await cdb.commit()
        except Exception:
            logger.exception(
                "platform_create_tenant_org_cleanup_failed",
                org_id=org_row.id,
            )
        await _rollback_tenant_owner_identity(owner_user_id, owner_user_created)
        await _rollback_zitadel_org()
        raise HTTPException(status_code=502, detail=f"Opslaan owner mislukt: {exc}") from exc

    invite_url_template = build_url_template(AuthLinkRoute.PASSWORD_SET)
    mail_sent = True
    try:
        await zitadel.send_invite_code(owner_user_id, url_template=invite_url_template)
    except Exception:
        logger.exception("platform_create_tenant_owner_mail_failed", email=body.owner_email)
        mail_sent = False

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
            "invite_mail_sent": mail_sent,
        },
    )
    message = (
        f"Tenant '{body.company_name}' aangemaakt. Provisioning gestart."
        if mail_sent
        else f"Tenant '{body.company_name}' aangemaakt, maar owner invite-mail kon niet worden verstuurd."
    )

    from app.services.listmonk import sync_portal_user_best_effort

    await sync_portal_user_best_effort(
        email=body.owner_email,
        name=f"{body.owner_first_name} {body.owner_last_name}".strip(),
        company=body.company_name,
        org_id=org_row.id,
        portal_user_id=getattr(owner_user_row, "id", None),
        zitadel_user_id=owner_user_id,
        source="portal_platform_create_tenant",
    )

    return CreateTenantResponse(
        org_id=org_row.id,
        slug=org_row.slug,
        owner_user_id=owner_user_id,
        message=message,
    )


def settings_zitadel_portal_org_id() -> str:
    """Lazy settings access (avoids a top-level config import cycle)."""
    from app.core.config import settings

    return settings.zitadel_portal_org_id


__all__ = ["router"]
