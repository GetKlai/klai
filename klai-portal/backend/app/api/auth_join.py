"""
Join request endpoint for SSO users without an org (SPEC-AUTH-006 R6).

POST /api/auth/join-request  -- Bearer auth required (OIDC token)
"""

from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user_id
from app.core.database import get_db
from app.models.portal import PortalJoinRequest
from app.services.join_request_token import generate_approval_token
from app.services.notifications import notify_admin_join_request
from app.services.zitadel import zitadel

logger = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["auth"])
bearer = HTTPBearer()

# C6.3: rate limit 3/day per zitadel_user_id
_RATE_LIMIT_PER_DAY = 3
_REQUEST_EXPIRY_DAYS = 7


class JoinRequestResponse(BaseModel):
    id: int
    status: str
    requested_at: str


@router.post("/auth/join-request", response_model=JoinRequestResponse)
async def create_join_request(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> JoinRequestResponse:
    """Create a join request for an SSO user with no portal_users row.

    Email is extracted from OIDC token only (never from request body).
    Idempotent: returns existing pending request if one exists.
    Rate-limited: 3 requests per day per zitadel_user_id.
    """
    # Get email from OIDC token
    info = await zitadel.get_userinfo(credentials.credentials)
    email = info.get("email", "")
    display_name = info.get("name", info.get("preferred_username", ""))

    # C5.2: one pending per zitadel_user_id (idempotent)
    # with_for_update prevents two concurrent requests from both inserting a duplicate
    pending_result = await db.execute(
        select(PortalJoinRequest)
        .where(
            PortalJoinRequest.zitadel_user_id == user_id,
            PortalJoinRequest.status == "pending",
        )
        .with_for_update()
    )
    existing = pending_result.scalar_one_or_none()
    if existing:
        return JoinRequestResponse(
            id=existing.id,
            status=existing.status,
            requested_at=str(existing.requested_at),
        )

    # C6.3: rate limit 3/day per zitadel_user_id
    today_start = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    count_result = await db.execute(
        select(func.count())
        .select_from(PortalJoinRequest)
        .where(
            PortalJoinRequest.zitadel_user_id == user_id,
            PortalJoinRequest.requested_at >= today_start,
        )
    )
    count = count_result.scalar_one()
    if count >= _RATE_LIMIT_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many join requests today. Please try again tomorrow.",
        )

    # Create the join request (approval_token needs id, so we use a placeholder first)
    new_request = PortalJoinRequest(
        zitadel_user_id=user_id,
        email=email,
        display_name=display_name,
        org_id=None,  # No org yet
        status="pending",
        approval_token="placeholder",  # noqa: S106  # Will be updated after flush
        expires_at=datetime.now(tz=UTC) + timedelta(days=_REQUEST_EXPIRY_DAYS),
    )
    db.add(new_request)
    await db.flush()

    # Generate deterministic HMAC token using the assigned id
    new_request.approval_token = generate_approval_token(new_request.id, user_id)
    await db.commit()
    await db.refresh(new_request)

    logger.info(
        "Join request created",
        request_id=new_request.id,
        zitadel_user_id=user_id,
        email=email,
    )

    # C7.3: email failure never blocks join request creation
    try:
        await notify_admin_join_request(email=email, display_name=display_name)
    except Exception:
        logger.warning("Admin notification failed for join request", request_id=new_request.id)

    return JoinRequestResponse(
        id=new_request.id,
        status=new_request.status,
        requested_at=str(new_request.requested_at),
    )
