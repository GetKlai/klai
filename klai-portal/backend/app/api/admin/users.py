"""Admin user lifecycle endpoints."""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.plans import get_plan_products
from app.models.groups import PortalGroup, PortalGroupMembership
from app.models.portal import PortalOrg, PortalUser
from app.models.products import PortalUserProduct
from app.services.audit import log_event
from app.services.github import remove_github_org_member
from app.services.zitadel import zitadel

from . import _get_caller_org, _require_admin, bearer

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
    "group-admin": None,
    "member": None,
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
    role: Literal["admin", "group-admin", "member"]
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
    role: Literal["admin", "group-admin", "member"] = "member"
    preferred_language: Literal["nl", "en"] = "nl"


class InviteResponse(BaseModel):
    user_id: str
    message: str


class UserUpdateRequest(BaseModel):
    first_name: str
    last_name: str
    preferred_language: Literal["nl", "en"]


class RoleUpdateRequest(BaseModel):
    role: Literal["admin", "group-admin", "member"]


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/users", response_model=UsersResponse)
async def list_users(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> UsersResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Get portal membership records (mapping + created_at + role)
    result = await db.execute(select(PortalUser).where(PortalUser.org_id == org.id).order_by(PortalUser.created_at))
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
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> InviteResponse:
    admin_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Seat enforcement: lock the org row to prevent concurrent invites
    locked_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org.id).with_for_update())
    org = locked_result.scalar_one()

    # TODO: filter by status == 'active' once AUTH-001 adds status column
    active_count = await db.scalar(select(func.count()).select_from(PortalUser).where(PortalUser.org_id == org.id))
    if active_count is not None and active_count >= org.seats:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Seat limit reached")

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

    user_row = PortalUser(
        zitadel_user_id=zitadel_user_id,
        org_id=org.id,
        role=body.role,
        preferred_language=body.preferred_language,
    )
    db.add(user_row)

    # Auto-assign products based on org plan
    plan_products = get_plan_products(org.plan)
    for product in plan_products:
        db.add(
            PortalUserProduct(
                zitadel_user_id=zitadel_user_id,
                org_id=org.id,
                product=product,
                enabled_by=admin_user_id,
            )
        )

    await db.commit()
    logger.info("User invited: email=%s, role=%s, org_id=%d", body.email, body.role, org.id)

    # Create personal KB for the new user. Fail-loud: if this raises, the 500
    # surfaces in the admin UI. The Zitadel invite + portal_users row are
    # already committed above, so retrying the invite is safe (idempotent) —
    # the personal KB helper uses ON CONFLICT DO NOTHING semantics.
    from app.services.default_knowledge_bases import create_default_personal_kb

    await create_default_personal_kb(zitadel_user_id, org.id, db)
    await db.commit()

    return InviteResponse(
        user_id=zitadel_user_id,
        message=f"Uitnodiging verstuurd naar {body.email}.",
    )


@router.patch("/users/{zitadel_user_id}", response_model=MessageResponse)
async def update_user(
    zitadel_user_id: str,
    body: UserUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
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
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.role = body.role
    await db.commit()
    logger.info("Role changed: user_id=%s, new_role=%s, org_id=%d", zitadel_user_id, body.role, org.id)

    return MessageResponse(message="Rol bijgewerkt.")


@router.post("/users/{zitadel_user_id}/resend-invite", response_model=MessageResponse)
async def resend_invite(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        await zitadel.resend_init_mail(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to resend invitation: {exc}",
        ) from exc

    return MessageResponse(message="Invitation resent.")


@router.delete("/users/{zitadel_user_id}", response_model=MessageResponse)
async def remove_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Verify user belongs to this org before deleting
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

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

    return MessageResponse(message="User deleted.")


@router.post("/users/{zitadel_user_id}/suspend", response_model=MessageResponse)
async def suspend_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Suspend an active user. Preserves group memberships and products."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
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
        org_id=org.id,
        actor=caller_id,
        action="user.suspended",
        resource_type="user",
        resource_id=zitadel_user_id,
    )
    await db.commit()
    return MessageResponse(message=f"User {zitadel_user_id} suspended.")


@router.post("/users/{zitadel_user_id}/reactivate", response_model=MessageResponse)
async def reactivate_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Reactivate a suspended user."""
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
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


@router.post("/users/{zitadel_user_id}/offboard", response_model=MessageResponse)
async def offboard_user(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """Offboard a user: remove memberships + products, deactivate in Zitadel, set status."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.status == "offboarded":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User has already been offboarded")

    # Cascade: remove group memberships and product assignments.
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
            PortalGroupMembership.group_id.in_(select(PortalGroup.id).where(PortalGroup.org_id == org.id)),
        )
    )
    # AsyncSession.execute() is typed as Result[Any]; the rowcount attribute
    # is only on CursorResult, hence getattr with a 0 default to satisfy pyright.
    memberships_removed_count = getattr(membership_delete_result, "rowcount", 0) or 0
    await db.execute(
        delete(PortalUserProduct).where(
            PortalUserProduct.zitadel_user_id == zitadel_user_id,
            PortalUserProduct.org_id == org.id,
        )
    )

    user.status = "offboarded"
    # SPEC-SEC-TENANT-001 REQ-1.4: structured event for VictoriaLogs audit so
    # any future cross-tenant regression is queryable. structlog kwargs land
    # as top-level JSON keys (queryable as `org_id:<n>`,
    # `memberships_removed_count:<n>` in LogsQL) — not under an `extra` blob.
    _slog.info(
        "user_offboarded",
        org_id=org.id,
        zitadel_user_id=zitadel_user_id,
        memberships_removed_count=memberships_removed_count,
    )
    await log_event(
        org_id=org.id,
        actor=caller_id,
        action="user.offboarded",
        resource_type="user",
        resource_id=zitadel_user_id,
    )
    await zitadel.deactivate_user(settings.zitadel_portal_org_id, zitadel_user_id)
    if user.github_username:
        await remove_github_org_member(user.github_username)
    else:
        logger.info("GitHub offboarding skipped for %s: no github_username linked", zitadel_user_id)
    await db.commit()
    return MessageResponse(message=f"User {zitadel_user_id} offboarded.")


# ---------------------------------------------------------------------------
# R6: Admin handover (SPEC-AUTH-009)
# ---------------------------------------------------------------------------

from app.services.events import emit_event  # noqa: E402 -- late import to avoid circular


@router.post("/users/{zitadel_user_id}/promote-admin", response_model=MessageResponse)
async def promote_admin(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """C6.1: Promote an active member to admin. No max-admin limit."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    target.role = "admin"
    await db.commit()
    logger.info(
        "promote_admin: actor=%s promoted user=%s in org=%d",
        caller_id,
        zitadel_user_id,
        org.id,
    )
    emit_event("user.role_promoted", org_id=org.id, user_id=zitadel_user_id)
    return MessageResponse(message=f"User {zitadel_user_id} promoted to admin.")


@router.post("/users/{zitadel_user_id}/demote-admin", response_model=MessageResponse)
async def demote_admin(
    zitadel_user_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """C6.2: Demote an admin to member. Refuses if this would leave zero admins."""
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == zitadel_user_id,
            PortalUser.org_id == org.id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # C6.2: target must currently be admin
    if target.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not an admin",
        )

    # @MX:ANCHOR SPEC-AUTH-009 R6 -- min-1-admin invariant: count admins BEFORE demoting.
    # @MX:REASON Concurrent demotes without this check leave workspaces orphaned.
    admin_count = await db.scalar(
        select(func.count())
        .select_from(PortalUser)
        .where(
            PortalUser.org_id == org.id,
            PortalUser.role == "admin",
        )
    )
    if (admin_count or 0) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot demote: this is the last admin. Promote another user first.",
        )

    target.role = "member"
    await db.commit()
    logger.info(
        "demote_admin: actor=%s demoted user=%s in org=%d",
        caller_id,
        zitadel_user_id,
        org.id,
    )
    emit_event("user.role_demoted", org_id=org.id, user_id=zitadel_user_id)
    return MessageResponse(message=f"User {zitadel_user_id} demoted to member.")


@router.delete("/users/me", response_model=MessageResponse)
async def leave_workspace(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """C6.3: Leave the workspace (self-removal). Refuses if caller is last admin.
    C6.7: Refuses if this would leave the workspace with zero users (last-member case).
    """
    caller_id, org, caller_user = await _get_caller_org(credentials, db)

    # @MX:ANCHOR SPEC-AUTH-009 R6 C6.3/C6.7 -- enforce min-1-admin and no-zombie-org.
    # @MX:REASON Last-admin and sole-member edge cases both result in an unmanageable workspace.
    if caller_user.role == "admin":
        admin_count = await db.scalar(
            select(func.count())
            .select_from(PortalUser)
            .where(
                PortalUser.org_id == org.id,
                PortalUser.role == "admin",
            )
        )
        if (admin_count or 0) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Promote another admin or delete the workspace before leaving.",
            )

    await db.delete(caller_user)
    await db.commit()
    logger.info("leave_workspace: user=%s left org=%d", caller_id, org.id)
    emit_event("user.left_workspace", org_id=org.id, user_id=caller_id)
    return MessageResponse(message="You have left the workspace.")
