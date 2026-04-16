"""
Workspace selection endpoint for multi-org SSO users (SPEC-AUTH-006 R9).

POST /api/auth/select-workspace  -- no Bearer required, uses ref from Redis
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg
from app.services.events import emit_event
from app.services.pending_session import PendingSessionService
from app.services.zitadel import zitadel

logger = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["auth"])

pending_session_svc = PendingSessionService()


class SelectWorkspaceRequest(BaseModel):
    ref: str
    org_id: int


class SelectWorkspaceResponse(BaseModel):
    workspace_url: str


@router.post("/auth/select-workspace", response_model=SelectWorkspaceResponse)
async def select_workspace(
    body: SelectWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
) -> SelectWorkspaceResponse:
    """Select a workspace from a pending multi-org SSO session.

    The ref is a UUID stored in Redis by idp_callback when the user
    has multiple portal_users rows. This endpoint consumes (one-time use)
    the pending session, finalizes the auth request for the selected org,
    and returns the workspace URL.
    """
    # Consume pending session (one-time use)
    session_data = await pending_session_svc.consume(body.ref)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    # Validate that the selected org_id is in the allowed list
    allowed_org_ids = session_data.get("org_ids", [])
    if body.org_id not in allowed_org_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisation not available for this user",
        )

    # Look up the org to get slug for workspace URL
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == body.org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )

    # Finalize the auth request
    session_id = session_data["session_id"]
    session_token = session_data["session_token"]
    auth_request_id = session_data["auth_request_id"]

    try:
        await zitadel.finalize_auth_request(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
        )
    except Exception as exc:
        logger.exception("finalize_auth_request failed in select-workspace")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again",
        ) from exc

    workspace_url = f"https://{org.slug}.{settings.domain}"

    emit_event(
        "login",
        user_id=session_data.get("zitadel_user_id"),
        properties={"method": "idp", "multi_org": True, "org_id": body.org_id},
    )

    logger.info(
        "Workspace selected",
        zitadel_user_id=session_data.get("zitadel_user_id"),
        org_id=body.org_id,
        workspace_url=workspace_url,
    )

    return SelectWorkspaceResponse(workspace_url=workspace_url)
