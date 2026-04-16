"""
Admin endpoints for managing join requests (SPEC-AUTH-006 R8).

GET    /api/admin/join-requests            -- list pending requests for org
POST   /api/admin/join-requests/{id}/approve -- approve and create portal_users row
POST   /api/admin/join-requests/{id}/deny    -- deny request
"""

from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import _get_caller_org, _require_admin, bearer
from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalJoinRequest, PortalUser
from app.services.join_request_token import verify_approval_token
from app.services.notifications import notify_user_join_approved

logger = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class JoinRequestItem(BaseModel):
    id: int
    zitadel_user_id: str
    email: str
    display_name: str | None
    status: str
    requested_at: str


class JoinRequestsResponse(BaseModel):
    requests: list[JoinRequestItem]


class ApproveResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/join-requests", response_model=JoinRequestsResponse)
async def list_join_requests(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> JoinRequestsResponse:
    """List pending join requests for the caller's org."""
    _zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalJoinRequest)
        .where(
            PortalJoinRequest.org_id == org.id,
            PortalJoinRequest.status == "pending",
        )
        .order_by(PortalJoinRequest.requested_at.desc())
    )
    rows = result.scalars().all()

    return JoinRequestsResponse(
        requests=[
            JoinRequestItem(
                id=row.id,
                zitadel_user_id=row.zitadel_user_id,
                email=row.email,
                display_name=row.display_name,
                status=row.status,
                requested_at=str(row.requested_at),
            )
            for row in rows
        ]
    )


@router.post("/join-requests/{request_id}/approve", response_model=ApproveResponse)
async def approve_join_request(
    request_id: int,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    token: str | None = Query(default=None),
) -> ApproveResponse:
    """Approve a join request — creates a portal_users row.

    Can be called with:
    - Bearer token (admin UI) — requires admin role
    - ?token= query param (email one-click link) — no Bearer required
    """
    # Token-based approval (email link)
    if token:
        jr_result = await db.execute(
            select(PortalJoinRequest).where(
                PortalJoinRequest.id == request_id,
                PortalJoinRequest.status == "pending",
            )
        )
        jr = jr_result.scalar_one_or_none()
        if not jr:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

        if not verify_approval_token(token, jr.id, jr.zitadel_user_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid approval token")

        if jr.expires_at and jr.expires_at < datetime.now(tz=UTC):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Request has expired")

        reviewer = "email-link"
        org_id = jr.org_id
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This request has no organisation assigned. Approve it from the admin panel.",
            )
    else:
        # Bearer-based approval (admin UI)
        zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
        _require_admin(caller_user)

        jr_result = await db.execute(
            select(PortalJoinRequest).where(
                PortalJoinRequest.id == request_id,
                PortalJoinRequest.org_id == org.id,
                PortalJoinRequest.status == "pending",
            )
        )
        jr = jr_result.scalar_one_or_none()
        if not jr:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

        reviewer = zitadel_user_id
        org_id = org.id

    # Create portal_users row
    new_user = PortalUser(
        zitadel_user_id=jr.zitadel_user_id,
        org_id=org_id,
        role="member",
        status="active",
        display_name=jr.display_name,
        email=jr.email,
    )
    db.add(new_user)

    # Mark request as approved
    jr.status = "approved"
    jr.reviewed_at = datetime.now(tz=UTC)
    jr.reviewed_by = reviewer

    await db.commit()

    logger.info(
        "Join request approved",
        request_id=request_id,
        zitadel_user_id=jr.zitadel_user_id,
        org_id=org_id,
        reviewer=reviewer,
    )

    # Send approval notification email (non-blocking)
    workspace_url = f"https://{settings.domain}"
    try:
        await notify_user_join_approved(
            email=jr.email,
            display_name=jr.display_name or jr.email,
            workspace_url=workspace_url,
        )
    except Exception:
        logger.warning("Approval notification email failed", request_id=request_id)

    return ApproveResponse(message="Request approved")


@router.post("/join-requests/{request_id}/deny", status_code=status.HTTP_204_NO_CONTENT)
async def deny_join_request(
    request_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Deny a join request."""
    zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    jr_result = await db.execute(
        select(PortalJoinRequest).where(
            PortalJoinRequest.id == request_id,
            PortalJoinRequest.org_id == org.id,
            PortalJoinRequest.status == "pending",
        )
    )
    jr = jr_result.scalar_one_or_none()
    if not jr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found")

    jr.status = "denied"
    jr.reviewed_at = datetime.now(tz=UTC)
    jr.reviewed_by = zitadel_user_id
    await db.commit()

    logger.info(
        "Join request denied",
        request_id=request_id,
        zitadel_user_id=jr.zitadel_user_id,
        org_id=org.id,
        reviewer=zitadel_user_id,
    )
