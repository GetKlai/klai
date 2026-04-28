"""Admin user lifecycle endpoints."""

import logging
from datetime import datetime
from typing import Literal

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

    try:
        await zitadel.grant_user_role(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            role="org:owner",
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
            PortalGroupMembership.group_id.in_(
                select(PortalGroup.id).where(PortalGroup.org_id == org.id)
            ),
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
    # any future cross-tenant regression is queryable.
    logger.info(
        "user_offboarded",
        extra={
            "event": "user_offboarded",
            "org_id": org.id,
            "zitadel_user_id": zitadel_user_id,
            "memberships_removed_count": memberships_removed_count,
        },
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
