"""Admin user lifecycle endpoints."""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import (
    ProfileRole,
    UserPermissions,
    get_caller,
    get_caller_at_least,
)

# SPEC-PORTAL-PRICING-PER-USER-001 Phase 3 (2026-05-12): the
# ``assert_role_allowed_for_plan`` import from ``app.core.profiles`` is
# gone. The function is a deprecated no-op there for the one-release
# transition window and this file no longer calls it. Phase 6 deletes
# the function entirely.
from app.models.groups import PortalGroup, PortalGroupMembership
from app.models.portal import PortalOrg, PortalUser
from app.services.audit import log_event
from app.services.auth_links import AuthLinkRoute, build_url_template
from app.services.github import remove_github_org_member
from app.services.kb_offboarding import (
    KbDisposition,
    OffboardPreview,
    apply_dispositions,
    compute_offboard_preview,
    revoke_user_credentials,
)
from app.services.mcp_role_notifier import fire_role_change_notification
from app.services.user_memberships import get_user_membership_summary
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)
# Structured-event logger for VictoriaLogs queryability — follows the
# dual-logger pattern established in app/api/auth.py. Per
# .claude/rules/klai/projects/portal-logging-py.md, all NEW log statements
# in this file go via structlog so kwargs land as queryable JSON keys
# instead of an `extra` blob. The legacy `logger` calls in this file
# pre-date that rule and remain on stdlib until a dedicated migration.
_slog = structlog.get_logger()

# SPEC-SEC-TENANT-001 REQ-2.2 (v0.5.0 / β): frozen module-level mapping from
# the portal role (InviteRequest.role Literal) to the optional Zitadel
# project-role string used by `zitadel.grant_user_role`. The mapping is
# exhaustive for the three accepted values of the Literal — REQ-2.3
# enforces this at runtime.
#
# Authority model: portal_users.role is the canonical source for portal-side
# authorization (admin / group-admin / member). Zitadel project roles are
# reserved for the one downstream signal that retrieval-api currently
# honours (org:owner ⇔ portal admin). Non-admin invites receive NO Zitadel
# grant; their JWT roles claim is empty and `_extract_role` returns None.
#
# Canonical doc + verification recipe:
# `.claude/rules/klai/platform/zitadel.md` "Project roles and JWT claims".
_ZITADEL_ROLE_BY_PORTAL_ROLE: Final[Mapping[str, str | None]] = {
    "admin": "org:owner",
    "group_manager": None,
    "kb_manager": None,
    "company": None,
    "personal": None,
}

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    zitadel_user_id: str
    email: str
    first_name: str
    last_name: str
    role: Literal["personal", "company", "kb_manager", "group_manager", "admin"]
    # SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0: account-type derived from
    # ``role`` via ``suggest_seat``. Surfaced here so /admin/users can
    # render the account-type column without an extra round-trip.
    seat_type: Literal["chat", "knowledge"]
    preferred_language: Literal["nl", "en"]
    status: str
    created_at: datetime
    invite_pending: bool


class UsersResponse(BaseModel):
    users: list[UserOut]


class InviteRequest(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    role: Literal["personal", "company", "kb_manager", "group_manager", "admin"] = "company"
    preferred_language: Literal["nl", "en"] = "nl"
    # SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0: account-type is derived
    # from ``role`` server-side via ``suggest_seat(role)``. The Phase 2
    # ``seat_type`` body field (decoupled-axes admin override) is gone
    # — the FE no longer surfaces a tier selector. PATCH /seat remains
    # callable for admin-tooling escape-hatch but is no longer in the
    # invite path. Old clients that still send ``seat_type`` get a
    # pydantic ``extra='ignore'`` (default) silent drop; the server
    # derives the canonical value regardless.


class InviteResponse(BaseModel):
    user_id: str
    message: str


class UserUpdateRequest(BaseModel):
    first_name: str
    last_name: str
    preferred_language: Literal["nl", "en"]


class RoleUpdateRequest(BaseModel):
    role: Literal["personal", "company", "kb_manager", "group_manager", "admin"]


class SeatUpdateRequest(BaseModel):
    """Body for ``PATCH /api/admin/users/{zitadel_user_id}/seat``.

    SPEC-PORTAL-PRICING-PER-USER-001 Phase 2 (introduced) / v0.5.0
    (still callable, no FE surface). Admin-tooling escape-hatch for
    force-overriding the role-derived account type. The FE no longer
    exposes this — invite + role-change both go through
    ``suggest_seat`` server-side. Use ``PATCH .../role`` for the
    other axis.
    """

    seat_type: Literal["chat", "knowledge"]


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UsersResponse)
async def list_users(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> UsersResponse:
    # Get portal membership records (mapping + created_at + role)
    result = await db.execute(
        select(PortalUser).where(PortalUser.org_id == perms.org_id).order_by(PortalUser.created_at)
    )
    portal_users = {u.zitadel_user_id: u for u in result.scalars().all()}

    if not portal_users:
        return UsersResponse(users=[])

    # Fetch live identity details from Zitadel (all users live in portal org)
    zitadel_users = await zitadel.list_org_users(settings.zitadel_portal_org_id)

    users_out: list[UserOut] = []
    for z in zitadel_users:
        uid = z.get("id", "")
        if uid not in portal_users:
            continue  # not in our portal (e.g. service accounts)
        profile = z.get("human", {}).get("profile", {})
        email_obj = z.get("human", {}).get("email", {})
        portal_user = portal_users[uid]
        users_out.append(
            UserOut(
                zitadel_user_id=uid,
                email=email_obj.get("email", ""),
                first_name=profile.get("firstName", ""),
                last_name=profile.get("lastName", ""),
                role=portal_user.role,
                seat_type=portal_user.seat_type,  # Phase 2: surface billing axis
                preferred_language=portal_user.preferred_language,
                status=portal_user.status,
                created_at=portal_user.created_at,
                invite_pending=z.get("state") == "USER_STATE_INITIAL",
            )
        )

    return UsersResponse(users=users_out)


@router.post("/users/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    # Lock the org row to prevent concurrent invites racing on portal_users
    # INSERT. We still need the row for Zitadel-side state (zitadel org id
    # is read from settings, but org.id / org.plan / billing snapshots may
    # be inspected by downstream logic — keep the lock to preserve
    # serializable semantics across the invite transaction).
    locked_result = await db.execute(select(PortalOrg).where(PortalOrg.id == perms.org_id).with_for_update())
    org = locked_result.scalar_one()

    # SPEC-PORTAL-PRICING-PER-USER-001 Phase 3 (2026-05-12) — removed
    # gates:
    #   * ``assert_role_allowed_for_plan(body.role, org.plan)``: the
    #     plan-ceiling on assignable role is gone. Role is the permissions
    #     axis only; seat_type is the billing axis. Admin can assign any
    #     role independently of plan; mismatches with the assigned seat
    #     are surfaced as a non-blocking warning in the invite modal
    #     (AC-5) and reconciled in Phase 4's capability resolver.
    #   * ``if active_count >= org.seats: raise 409 Seat limit reached``:
    #     the hard seat-cap is gone. Headcount is derived from active
    #     users via /admin/billing/breakdown; Phase 5 prorates the bill
    #     from portal_user_seat_history per active seat per day.
    # Both removals are guarded by ``rules/no-portal-plan-gate.yml`` (ast-
    # grep) so a future refactor cannot silently reintroduce them.

    try:
        user_data = await zitadel.invite_user(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            preferred_language=body.preferred_language,
        )
    except Exception as exc:
        logger.exception("User invite failed for %s: %s", body.email, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to invite user: {exc}",
        ) from exc

    zitadel_user_id: str = user_data["userId"]

    # SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-2: the user now exists in Zitadel
    # but no invite mail was sent (invite_user used sendCodes=False). Issue
    # the invite code with an explicit Klai urlTemplate so the activation
    # link in the mail lands on my.getklai.com/password/set, not on
    # auth.getklai.com/ui/login/. If this second call fails after the first
    # succeeded, we return 502 with the orphan userId so an admin can recover
    # via the resend-invite endpoint (which re-issues only call 2).
    invite_url_template = build_url_template(AuthLinkRoute.PASSWORD_SET)
    try:
        await zitadel.send_invite_code(zitadel_user_id, url_template=invite_url_template)
    except Exception as exc:
        _slog.exception(
            "invite_partial_user_created",
            zitadel_user_id=zitadel_user_id,
            email=body.email,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "invite_partial_failure",
                "user_id": zitadel_user_id,
                "message": "User created but invite mail failed — retry via resend-invite",
            },
        ) from exc

    # SPEC-SEC-TENANT-001 REQ-2 (v0.5.0 / β): only portal_role="admin" gets a
    # Zitadel grant; group-admin and member rely on portal_users.role for
    # authorization. v0.1 hardcoded role="org:owner" for every invite — the
    # finding #10 time-bomb. v0.5.0 keeps the admin grant as before and
    # explicitly skips the Zitadel call for non-admins.
    try:
        zitadel_role = _ZITADEL_ROLE_BY_PORTAL_ROLE[body.role]
    except KeyError as exc:
        # REQ-2.3: pydantic Literal blocks this at parse time; the runtime
        # check exists to keep the mapping and the InviteRequest schema in
        # lock-step. Reaching this branch means the schema added a value the
        # mapping has not — a developer error, not a user-supplied input.
        logger.exception(
            "invite_role_not_in_mapping",
            extra={"portal_role": body.role, "email": body.email},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unsupported role",
        ) from exc

    if zitadel_role is None:
        # REQ-2.1 observability: structured event so the absence-of-grant is
        # queryable in VictoriaLogs (e.g. confirm zero org:* grants land for
        # non-admin invites in production).
        _slog.info(
            "invite_no_zitadel_grant",
            org_id=org.id,
            portal_role=body.role,
            zitadel_user_id=zitadel_user_id,
        )
    else:
        try:
            await zitadel.grant_user_role(
                org_id=settings.zitadel_portal_org_id,
                user_id=zitadel_user_id,
                role=zitadel_role,
            )
        except Exception as exc:
            logger.exception("Role grant failed for invited user %s: %s", body.email, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to assign project role: {exc}",
            ) from exc

    # SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0: account-type is DERIVED
    # from role server-side. The Phase 2 ``body.seat_type`` override
    # path is gone — InviteRequest no longer carries that field, and
    # even if a legacy client sends it pydantic drops it silently. The
    # FE displays the derived tier as a read-only badge that updates
    # when the Profile dropdown changes.
    from app.core.seats import suggest_seat

    seat_type_value = str(suggest_seat(body.role))

    user_row = PortalUser(
        zitadel_user_id=zitadel_user_id,
        org_id=org.id,
        role=body.role,
        seat_type=seat_type_value,
        preferred_language=body.preferred_language,
    )
    db.add(user_row)

    # SPEC-PORTAL-RBAC-001: products are derived from (role, plan,
    # platform_unlocked_features) at read time; no per-user entitlement
    # rows are written here.
    #
    # Personal KB is created in the SAME transaction as the portal_users INSERT
    # so that the tenant-scoped GUC (`app.current_org_id`) is still active when
    # writing to portal_knowledge_bases (Category-D RLS). Splitting the commit
    # would clear the GUC and trip the strict RLS policy on the KB INSERT —
    # exact same shape as the "Post-commit db.refresh on RLS tables" pitfall in
    # .claude/rules/klai/projects/portal-backend.md. If the KB-creation step
    # fails, the whole transaction rolls back; the Zitadel-side invite is left
    # behind (rollback can't undo the external API call) and a retry will hit
    # Zitadel's 409 path — caller is expected to surface that as a clear error
    # rather than the previous half-state where portal_users existed without a
    # personal KB and the admin saw a 500.
    from app.services.default_knowledge_bases import create_default_personal_kb

    await create_default_personal_kb(zitadel_user_id, org.id, db)
    await db.commit()
    # SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-8: emit url_template_host so a
    # VictoriaLogs query can prove the link host across all invites.
    _slog.info(
        "invite_user",
        email=body.email,
        role=body.role,
        org_id=org.id,
        zitadel_user_id=zitadel_user_id,
        url_template_host=urlparse(invite_url_template).netloc,
    )

    return InviteResponse(
        user_id=zitadel_user_id,
        message=f"Uitnodiging verstuurd naar {body.email}.",
    )


@router.patch("/users/{zitadel_user_id}", response_model=MessageResponse)
async def update_user(
    zitadel_user_id: str,
    body: UserUpdateRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        await zitadel.update_user_profile(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            first_name=body.first_name,
            last_name=body.last_name,
            preferred_language=body.preferred_language,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to update user: {exc}",
        ) from exc

    user.preferred_language = body.preferred_language
    await db.commit()

    return MessageResponse(message="User updated.")


@router.patch("/users/{zitadel_user_id}/role", response_model=MessageResponse)
async def update_user_role(
    zitadel_user_id: str,
    body: RoleUpdateRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """SPEC-PORTAL-ADMIN-UI-001 REQ-2: unified change-profile endpoint.

    Serialises role changes per-org and refuses to demote the last admin so
    the new admin UI cannot lock a tenant out of its own workspace. Mirrors
    the invariant that POST /demote-admin already enforces.
    """
    # @MX:ANCHOR SPEC-PORTAL-ADMIN-UI-001 REQ-2 — min-1-admin invariant.
    # @MX:REASON Without serialised role changes two concurrent admin->X
    # patches that both see admin_count=2 can each succeed and leave the
    # workspace with zero admins. We need both the lock AND the org's
    # current plan; ``_lock_org_for_role_change`` only locks by id, so
    # we replicate the FOR UPDATE query here to read ``plan`` from the
    # locked row.
    locked_org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == perms.org_id).with_for_update())
    locked_org = locked_org_result.scalar_one()

    # SPEC-PORTAL-PRICING-PER-USER-001 Phase 3 (2026-05-12): plan-ceiling
    # role check removed (was REQ-12 / REQ-13). Role is decoupled from
    # plan; admin can change to any role independently. Seat-tier mismatch
    # surfaces in the admin UI as a non-blocking warning.
    _ = locked_org  # retained lock for serializable semantics

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Refuse to demote the last admin via the unified endpoint.
    if user.role == "admin" and body.role != "admin":
        admin_count = await db.scalar(
            select(func.count())
            .select_from(PortalUser)
            .where(
                PortalUser.org_id == perms.org_id,
                PortalUser.role == "admin",
            )
        )
        if (admin_count or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot change profile: this is the last admin. Promote another user first.",
            )

    user.role = body.role
    await db.commit()
    logger.info("Role changed: user_id=%s, new_role=%s, org_id=%d", zitadel_user_id, body.role, perms.org_id)

    # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-14 + REQ-18: fan out a role-change
    # notification to klai-knowledge-mcp so active MCP sessions for this
    # user receive ``notifications/tools/list_changed`` and reload their
    # tool-list under the new role's filter. Fire-and-forget — the
    # role-change response never waits on the cross-service hop.
    fire_role_change_notification(zitadel_user_id)

    return MessageResponse(message="Rol bijgewerkt.")


@router.patch("/users/{zitadel_user_id}/seat", response_model=MessageResponse)
async def update_user_seat(
    zitadel_user_id: str,
    body: SeatUpdateRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """SPEC-PORTAL-PRICING-PER-USER-001 Phase 2 — change a user's billing tier.

    Independent from ``PATCH .../role``: admin can move a user's seat
    without touching their role. The Postgres trigger on portal_users
    appends a ``seat_change`` row to ``portal_user_seat_history``; this
    handler also emits a ``user.seat_changed`` audit-log event with the
    cost-delta so customer-support questions ("why did our bill go up?")
    can be answered from one query.

    No-op (200) when the requested seat equals the current value.
    """
    from app.core.seats import SeatType, monthly_seat_cost

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    old_seat = user.seat_type
    new_seat = body.seat_type
    if old_seat == new_seat:
        return MessageResponse(message="Seat ongewijzigd.")

    user.seat_type = new_seat
    await db.commit()
    logger.info(
        "Seat changed: user_id=%s, old=%s, new=%s, org_id=%d",
        zitadel_user_id,
        old_seat,
        new_seat,
        perms.org_id,
    )

    # Cost delta surfaces in the audit detail so a CSV export of the audit
    # log doubles as a per-org billing-change trail. Positive delta = price
    # went up, negative = price went down.
    cost_delta = monthly_seat_cost(SeatType(new_seat)) - monthly_seat_cost(SeatType(old_seat))
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="user.seat_changed",
        resource_type="portal_user",
        resource_id=zitadel_user_id,
        details={
            "old_seat": old_seat,
            "new_seat": new_seat,
            "cost_delta_eur": cost_delta,
        },
    )

    return MessageResponse(message="Seat bijgewerkt.")


@router.post("/users/{zitadel_user_id}/resend-invite", response_model=MessageResponse)
async def resend_invite(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        # SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-3: replaces the legacy
        # resend_init_mail. Same v2 endpoint, but with an explicit Klai
        # urlTemplate. REQ-10 — always pass urlTemplate to defeat Zitadel's
        # per-user url_template cache (a previous Zitadel-default URL would
        # otherwise persist).
        invite_url_template = build_url_template(AuthLinkRoute.PASSWORD_SET)
        await zitadel.send_invite_code(zitadel_user_id, url_template=invite_url_template)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to resend invitation: {exc}",
        ) from exc

    # SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-8: same observability field on resend.
    _slog.info(
        "resend_invite",
        zitadel_user_id=zitadel_user_id,
        org_id=perms.org_id,
        url_template_host=urlparse(invite_url_template).netloc,
    )
    return MessageResponse(message="Invitation resent.")


@router.delete("/users/{zitadel_user_id}", response_model=MessageResponse)
async def remove_user(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if zitadel_user_id == perms.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use leave workspace instead of deleting your own account.",
        )

    # Verify user belongs to this org before deleting
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    membership_summary = await get_user_membership_summary(
        zitadel_user_id,
        excluding_org_id=perms.org_id,
    )
    if membership_summary.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Platform-admin identities cannot be deleted from a tenant admin surface.",
        )

    delete_global_identity = membership_summary.remaining_count == 0
    if delete_global_identity:
        try:
            await zitadel.remove_user(org_id=settings.zitadel_portal_org_id, zitadel_user_id=zitadel_user_id)
        except Exception as exc:
            logger.exception("User removal failed for user %s: %s", zitadel_user_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to delete user: {exc}",
            ) from exc

    await db.delete(user)
    await db.commit()
    fire_role_change_notification(zitadel_user_id)

    message = "User deleted." if delete_global_identity else "User removed from organization."
    return MessageResponse(message=message)


@router.post("/users/{zitadel_user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Suspend an active user. Preserves group memberships and products."""
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.status in ("suspended", "offboarded"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User has status '{user.status}' and cannot be suspended",
        )

    user.status = "suspended"
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="user.suspended",
        resource_type="user",
        resource_id=zitadel_user_id,
    )
    await db.commit()
    return MessageResponse(message=f"User {zitadel_user_id} suspended.")


@router.post("/users/{zitadel_user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Reactivate a suspended user."""
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.status != "suspended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User has status '{user.status}' and cannot be reactivated",
        )

    user.status = "active"
    await db.commit()
    return MessageResponse(message=f"User {zitadel_user_id} reactivated.")


@router.get("/users/{zitadel_user_id}/offboard-preview", response_model=OffboardPreview)
async def offboard_preview(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> OffboardPreview:
    """SPEC-PORTAL-KB-OWNERSHIP-001 REQ-2.1 — preview KB-dispositions for offboard.

    Returns the org KBs the user is the sole owner of (admin must choose
    transfer-to or delete), the personal KBs (always purged on offboard),
    and the count of API-keys / MCP-tokens that will be auto-revoked.
    The frontend uses this to render the offboard wizard.
    """
    # Verify user belongs to caller's tenant before exposing any data.
    user_result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return await compute_offboard_preview(zitadel_user_id, perms.org_id, db)


class OffboardRequest(BaseModel):
    """Body for ``POST /api/admin/users/{zitadel_user_id}/offboard``.

    REQ-2.5 — every KB returned by ``offboard-preview`` MUST appear here
    with an explicit disposition, otherwise we 400 with the missing list.
    No implicit defaults: silent-orphans are the failure mode this SPEC
    exists to prevent.
    """

    kb_dispositions: list[KbDisposition] = []


@router.post("/users/{zitadel_user_id}/offboard", response_model=MessageResponse)
async def offboard_user(
    zitadel_user_id: str,
    body: OffboardRequest | None = None,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Offboard a user: KB-dispositions + token-revoke + memberships + Zitadel.

    SPEC-PORTAL-KB-OWNERSHIP-001 Phase 3 expanded the surface of this
    endpoint. The request body is now mandatory whenever the offboard
    preview lists any KBs — see REQ-2.5. Order of operations inside the
    DB transaction:

      1. Validate body covers EVERY KB returned by the preview (REQ-2.5).
      2. Apply KB dispositions via ``apply_dispositions`` (REQ-2.2 .. 2.6 + 2.8).
      3. Revoke partner API keys + MCP tokens via ``revoke_user_credentials`` (REQ-2.7).
      4. Delete tenant-scoped group memberships (existing SEC-TENANT-001 logic).
      5. Flip user.status to 'offboarded' + emit user.offboarded audit.
      6. Commit DB transaction.
      7. After-commit: Zitadel deactivate + GitHub remove.

    Failure in steps 1-5 raises and rolls back the entire transaction —
    the user stays ``active`` and KBs are unchanged. Failure in step 7
    leaves the user ``offboarded`` in our DB but Zitadel/GitHub
    out-of-sync; this is the same fail-open behaviour the endpoint had
    before this SPEC and is fine for now (the cleanup-runbook lists it).
    """
    body = body or OffboardRequest()

    user_result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.status == "offboarded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User has already been offboarded")

    # REQ-2.5 — fetch the preview and verify the body covers every KB it
    # lists. Missing dispositions return 400 with the explicit slug list
    # so the frontend can re-render the wizard with the offending entries
    # highlighted.
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == perms.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Organisation not found")

    preview = await compute_offboard_preview(zitadel_user_id, perms.org_id, db)
    expected_kb_ids = {kb.kb_id for kb in preview.org_kbs_solely_owned} | {kb.kb_id for kb in preview.personal_kbs}
    provided_kb_ids = {d.kb_id for d in body.kb_dispositions}
    missing = expected_kb_ids - provided_kb_ids
    if missing:
        # Build a stable, human-readable list (slug-based) so the admin
        # can click straight to the affected KBs in the wizard.
        kb_lookup: dict[int, str] = {kb.kb_id: kb.slug for kb in (*preview.org_kbs_solely_owned, *preview.personal_kbs)}
        missing_slugs = sorted(kb_lookup[kb_id] for kb_id in missing)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "missing_kb_dispositions",
                "missing": missing_slugs,
                "message": f"Missing dispositions for: [{', '.join(missing_slugs)}]",
            },
        )

    # REQ-2.2 — KB dispositions inside the offboard tx.
    await apply_dispositions(
        target_user_id=zitadel_user_id,
        dispositions=body.kb_dispositions,
        actor_user_id=perms.user_id,
        org=org,
        db=db,
    )

    # REQ-2.7 — token revoke inside the same tx.
    api_keys_deleted, mcp_tokens_revoked = await revoke_user_credentials(
        target_user_id=zitadel_user_id,
        org_id=perms.org_id,
        db=db,
    )

    # Cascade: remove group memberships.
    #
    # SPEC-SEC-TENANT-001 REQ-1: scope the membership delete to the caller's
    # org via PortalGroup.org_id. PortalGroupMembership has no org_id column
    # (tenancy inherits via the parent group's FK), so a delete keyed only on
    # zitadel_user_id wipes the user's memberships in EVERY tenant they belong
    # to. The subselect constrains the rows to groups owned by the caller's
    # org. Memberships in other orgs are left untouched.
    membership_delete_result = await db.execute(
        delete(PortalGroupMembership).where(
            PortalGroupMembership.zitadel_user_id == zitadel_user_id,
            PortalGroupMembership.group_id.in_(select(PortalGroup.id).where(PortalGroup.org_id == perms.org_id)),
        )
    )
    # AsyncSession.execute() is typed as Result[Any]; the rowcount attribute
    # is only on CursorResult, hence getattr with a 0 default to satisfy pyright.
    memberships_removed_count = getattr(membership_delete_result, "rowcount", 0) or 0
    # SPEC-PORTAL-RBAC-001: portal_user_products is dropped; no rows to clean up.

    user.status = "offboarded"
    # SPEC-SEC-TENANT-001 REQ-1.4: structured event for VictoriaLogs audit so
    # any future cross-tenant regression is queryable. structlog kwargs land
    # as top-level JSON keys (queryable as `org_id:<n>`,
    # `memberships_removed_count:<n>` in LogsQL) — not under an `extra` blob.
    _slog.info(
        "user_offboarded",
        org_id=perms.org_id,
        zitadel_user_id=zitadel_user_id,
        memberships_removed_count=memberships_removed_count,
        kb_dispositions_count=len(body.kb_dispositions),
        api_keys_deleted=api_keys_deleted,
        mcp_tokens_revoked=mcp_tokens_revoked,
    )
    await log_event(
        org_id=perms.org_id,
        actor=perms.user_id,
        action="user.offboarded",
        resource_type="user",
        resource_id=zitadel_user_id,
        details={
            "kb_dispositions_count": len(body.kb_dispositions),
            "api_keys_deleted": api_keys_deleted,
            "mcp_tokens_revoked": mcp_tokens_revoked,
        },
    )
    await db.commit()

    # Post-commit external side-effects. Failures here leave us in the
    # documented "DB-side offboarded, IdP-side still active" state — same
    # behaviour as before this SPEC.
    await zitadel.deactivate_user(settings.zitadel_portal_org_id, zitadel_user_id)
    if user.github_username:
        await remove_github_org_member(user.github_username)
    else:
        logger.info("GitHub offboarding skipped for %s: no github_username linked", zitadel_user_id)
    return MessageResponse(message=f"User {zitadel_user_id} offboarded.")


# ---------------------------------------------------------------------------
# R6: Admin handover (SPEC-AUTH-009)
# ---------------------------------------------------------------------------

from app.services.events import emit_event  # noqa: E402 -- late import to avoid circular


@router.post("/users/{zitadel_user_id}/promote-admin", response_model=MessageResponse)
async def promote_admin(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """C6.1: Promote an active member to admin. No max-admin limit."""
    # Lock the org row first so the plan we validate against can't change
    # mid-request. ``perms.plan`` is a snapshot from request start; reading
    # from the locked row mirrors invite_user / update_user_role.
    locked_org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == perms.org_id).with_for_update())
    locked_org = locked_org_result.scalar_one()

    # SPEC-PORTAL-PRICING-PER-USER-001 Phase 3 (2026-05-12): the
    # ``assert_role_allowed_for_plan("admin", locked_org.plan)`` check is
    # gone. Plan ceilings no longer gate role assignment; promotion to
    # admin is permitted on every plan.
    _ = locked_org  # retained lock for serializable semantics

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.role = "admin"
    await db.commit()
    logger.info(
        "promote_admin: actor=%s promoted user=%s in org=%d",
        perms.user_id,
        zitadel_user_id,
        perms.org_id,
    )
    emit_event("user.role_promoted", org_id=perms.org_id, user_id=zitadel_user_id)
    # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-14 + REQ-18: see update_user_role for
    # rationale. Fire-and-forget cross-service hop.
    fire_role_change_notification(zitadel_user_id)
    return MessageResponse(message=f"User {zitadel_user_id} promoted to admin.")


async def _lock_org_for_role_change(db: AsyncSession, org_id: int) -> None:
    """Take a row-level lock on portal_orgs.{org_id} to serialise role changes.

    @MX:ANCHOR SPEC-AUTH-009 R6 — min-1-admin invariant requires serialised
    role changes. Without this lock, two concurrent demotes that both see
    admin_count=2 can each succeed and leave the workspace with zero admins.
    @MX:REASON SELECT...FOR UPDATE on the parent org row blocks any other
    transaction performing a role change on the same org until commit.
    Patterns chosen from .claude/rules/klai/projects/portal-backend.md
    "SELECT FOR UPDATE in get-or-create patterns" pitfall.
    """
    await db.execute(select(PortalOrg.id).where(PortalOrg.id == org_id).with_for_update())


@router.post("/users/{zitadel_user_id}/demote-admin", response_model=MessageResponse)
async def demote_admin(
    zitadel_user_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """C6.2: Demote an admin to member. Refuses if this would leave zero admins."""
    # Serialise concurrent role changes for this org (see _lock_org_for_role_change).
    await _lock_org_for_role_change(db, perms.org_id)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if target.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not an admin",
        )

    admin_count = await db.scalar(
        select(func.count())
        .select_from(PortalUser)
        .where(
            PortalUser.org_id == perms.org_id,
            PortalUser.role == "admin",
        )
    )
    if (admin_count or 0) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot demote: this is the last admin. Promote another user first.",
        )

    # SPEC-PORTAL-PROFILES-001 migration: "member" no longer exists in the
    # role enum. A demoted admin keeps org-knowledge read access, so demote
    # to "company" (the rung that mirrors what former members had).
    target.role = "company"
    await db.commit()
    logger.info(
        "demote_admin: actor=%s demoted user=%s in org=%d",
        perms.user_id,
        zitadel_user_id,
        perms.org_id,
    )
    emit_event("user.role_demoted", org_id=perms.org_id, user_id=zitadel_user_id)
    # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-14 + REQ-18: see update_user_role for
    # rationale. Fire-and-forget cross-service hop.
    fire_role_change_notification(zitadel_user_id)
    return MessageResponse(message=f"User {zitadel_user_id} demoted to company.")


@router.delete("/users/me", response_model=MessageResponse)
async def leave_workspace(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """C6.3: Leave the workspace (self-removal). Refuses if caller is last admin.
    C6.7: Refuses if this would leave the workspace with zero users (last-member case).
    """
    if perms.role == ProfileRole.ADMIN:
        # Serialise concurrent role changes for this org (see _lock_org_for_role_change).
        await _lock_org_for_role_change(db, perms.org_id)

        admin_count = await db.scalar(
            select(func.count())
            .select_from(PortalUser)
            .where(
                PortalUser.org_id == perms.org_id,
                PortalUser.role == "admin",
            )
        )
        if (admin_count or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Promote another admin or delete the workspace before leaving.",
            )

    # Re-fetch the caller's PortalUser ORM-object for db.delete. UserPermissions
    # is a snapshot, not the ORM row.
    caller_row_result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == perms.user_id,
            PortalUser.org_id == perms.org_id,
        )
    )
    caller_user = caller_row_result.scalar_one()
    await db.delete(caller_user)
    await db.commit()
    logger.info("leave_workspace: user=%s left org=%d", perms.user_id, perms.org_id)
    emit_event("user.left_workspace", org_id=perms.org_id, user_id=perms.user_id)
    return MessageResponse(message="You have left the workspace.")
