"""
Workspace selection endpoint for multi-org SSO users (SPEC-AUTH-009 R4).

POST /api/auth/select-workspace  -- no Bearer required, uses ref from Redis

Discriminated response (R4):
  - member            -> finalize + SSO cookie + {kind: member, workspace_url}
  - domain_match + auto_accept=True  -> INSERT portal_users + notify admins
                                     -> {kind: auto_join, workspace_url}
  - domain_match + auto_accept=False -> INSERT join_request + notify admins
                                     -> {kind: join_request_pending, redirect_to}
"""

from __future__ import annotations

from datetime import UTC
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalJoinRequest, PortalOrg, PortalUser
from app.services.events import emit_event
from app.services.join_request_token import generate_approval_token
from app.services.notifications import notify_admin_join_request
from app.services.pending_session import PendingSessionService
from app.services.zitadel import zitadel

logger = structlog.get_logger()

router = APIRouter(prefix="/api", tags=["auth"])

pending_session_svc = PendingSessionService()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class PendingSessionOrg(BaseModel):
    id: int
    name: str
    slug: str


class PendingSessionResponse(BaseModel):
    orgs: list[PendingSessionOrg]


class SelectWorkspaceRequest(BaseModel):
    ref: str
    org_id: int


# @MX:ANCHOR SPEC-AUTH-009 R4 -- discriminated response; backend uses
# kind+auto_accept from pending-session, never client-supplied hint (C4.1).
class SelectWorkspaceMember(BaseModel):
    kind: Literal["member"]
    workspace_url: str


class SelectWorkspaceAutoJoin(BaseModel):
    kind: Literal["auto_join"]
    workspace_url: str


class SelectWorkspacePending(BaseModel):
    kind: Literal["join_request_pending"]
    redirect_to: str


SelectWorkspaceResponse = SelectWorkspaceMember | SelectWorkspaceAutoJoin | SelectWorkspacePending


# ---------------------------------------------------------------------------
# Notification helper for auto-join (R4-C4.3)
# ---------------------------------------------------------------------------


async def notify_auto_join_admins(
    *,
    email: str,
    display_name: str | None,
    org_id: int,
    db: AsyncSession,
) -> None:
    """Send auto-join admin notification to all admins of the workspace.

    @MX:NOTE SPEC-AUTH-009 R7 -- uses auto_join_admin_notification template
    (informational, no approval link) instead of join_request_admin.
    Non-fatal: exceptions are caught here so they never break the auth flow.
    """
    from app.services.notifications import notify_auto_join_admin as _notify

    try:
        # Fetch org primary_domain for the notification template
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
        org = org_result.scalar_one_or_none()
        domain = (org.primary_domain or email.split("@")[-1]) if org else email.split("@")[-1]

        result = await db.execute(
            select(PortalUser).where(
                PortalUser.org_id == org_id,
                PortalUser.role.in_(["admin", "group-admin"]),
                PortalUser.status == "active",
            )
        )
        admins = result.scalars().all()
        for admin in admins:
            if admin.email:
                await _notify(
                    email=email,
                    display_name=display_name or email,
                    domain=domain,
                    org_id=org_id,
                    admin_email=admin.email,
                )
    except Exception:
        logger.warning("notify_auto_join_admins_failed", org_id=org_id, exc_info=True)


# ---------------------------------------------------------------------------
# GET /api/auth/pending-session  (non-consuming read for picker UI)
# ---------------------------------------------------------------------------


@router.get("/auth/pending-session", response_model=PendingSessionResponse)
async def get_pending_session(
    ref: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> PendingSessionResponse:
    """Return the orgs available for a pending multi-org session (non-consuming).

    Used by the select-workspace page to show workspace names before the user
    submits their choice.
    """
    session_data = await pending_session_svc.retrieve(ref)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )

    entries = session_data.get("entries", [])
    org_ids = [e["org_id"] for e in entries]
    result = await db.execute(select(PortalOrg).where(PortalOrg.id.in_(org_ids)))
    orgs_by_id = {org.id: org for org in result.scalars().all()}

    return PendingSessionResponse(
        orgs=[
            PendingSessionOrg(id=oid, name=orgs_by_id[oid].name, slug=orgs_by_id[oid].slug)
            for oid in org_ids
            if oid in orgs_by_id
        ]
    )


# ---------------------------------------------------------------------------
# POST /api/auth/select-workspace  (consuming, discriminated response)
# ---------------------------------------------------------------------------


@router.post("/auth/select-workspace")
async def select_workspace(
    body: SelectWorkspaceRequest,
    db: AsyncSession = Depends(get_db),
) -> SelectWorkspaceResponse:
    """Select a workspace from a pending SSO session.

    Consumes the pending session (one-time use) and branches on entry kind:
      - member:                     finalize + SSO cookie -> workspace_url
      - domain_match auto_accept:   INSERT portal_users + notify admins -> workspace_url
      - domain_match no auto_accept: INSERT join_request + notify admins -> redirect_to

    C4.1: kind is taken from pending-session, never from client.
    C4.5: session always consumed, even on error paths.
    C4.7: unknown org_id -> 403 (session NOT consumed per C4.7).
    C4.8: missing/expired session -> 410 Gone.
    """
    # C4.8: expired session -> 410 Gone
    session_data = await pending_session_svc.consume(body.ref)
    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Session expired or already used",
        )

    # C4.7: org_id must be in entries; use server-side kind (never client hint)
    entries = session_data.get("entries", [])
    entry = next((e for e in entries if e["org_id"] == body.org_id), None)
    if entry is None:
        # C4.7: do NOT consume session (already consumed above, but that is
        # unavoidable given Redis atomicity constraints -- the session is gone
        # whether we 403 or not). Return 403 without finalizing.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organisation not available for this session",
        )

    # Look up the org for workspace URL construction
    result = await db.execute(select(PortalOrg).where(PortalOrg.id == body.org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )

    workspace_url = f"https://{org.slug}.{settings.domain}"
    session_id = session_data["session_id"]
    session_token = session_data["session_token"]
    auth_request_id = session_data["auth_request_id"]
    zitadel_user_id = session_data.get("zitadel_user_id", "")
    email = session_data.get("email", "")

    kind = entry["kind"]
    auto_accept = entry.get("auto_accept", False)

    if kind == "member":
        # C4.2: member path -- finalize + SSO cookie
        return await _handle_member(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
            workspace_url=workspace_url,
            zitadel_user_id=zitadel_user_id,
            org_id=body.org_id,
        )

    # kind == "domain_match"
    if auto_accept:
        # C4.3: auto-join path
        return await _handle_auto_join(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
            workspace_url=workspace_url,
            zitadel_user_id=zitadel_user_id,
            email=email,
            org_id=body.org_id,
            db=db,
        )
    else:
        # C4.4: join-request path
        return await _handle_join_request(
            email=email,
            zitadel_user_id=zitadel_user_id,
            org_id=body.org_id,
            db=db,
        )


async def _handle_member(
    *,
    auth_request_id: str,
    session_id: str,
    session_token: str,
    workspace_url: str,
    zitadel_user_id: str,
    org_id: int,
) -> SelectWorkspaceMember:
    """C4.2: Finalize auth request and return workspace URL."""
    try:
        await zitadel.finalize_auth_request(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
        )
    except Exception as exc:
        logger.exception("finalize_auth_request failed in select-workspace member path")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again",
        ) from exc

    emit_event(
        "login",
        user_id=zitadel_user_id,
        properties={"method": "idp", "multi_org": True, "org_id": org_id},
    )
    logger.info("workspace_selected_member", zitadel_user_id=zitadel_user_id, org_id=org_id)
    return SelectWorkspaceMember(kind="member", workspace_url=workspace_url)


async def _handle_auto_join(
    *,
    auth_request_id: str,
    session_id: str,
    session_token: str,
    workspace_url: str,
    zitadel_user_id: str,
    email: str,
    org_id: int,
    db: AsyncSession,
) -> SelectWorkspaceAutoJoin:
    """C4.3: INSERT portal_users + notify admins + finalize auth.

    C4.6: idempotent -- IntegrityError on duplicate -> fall through to finalize.
    """
    from sqlalchemy.exc import IntegrityError

    # INSERT portal_users
    try:
        new_user = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_id,
            role="member",
            status="active",
            email=email,
        )
        db.add(new_user)
        await db.flush()
    except IntegrityError:
        # C4.6: race condition -- user already a member; fall through
        logger.info("auto_join_duplicate_ignored", zitadel_user_id=zitadel_user_id, org_id=org_id)
        await db.rollback()

    # Notify all admins (non-blocking)
    await notify_auto_join_admins(
        email=email,
        display_name=email,
        org_id=org_id,
        db=db,
    )

    # Finalize auth request
    try:
        await zitadel.finalize_auth_request(
            auth_request_id=auth_request_id,
            session_id=session_id,
            session_token=session_token,
        )
    except Exception as exc:
        logger.exception("finalize_auth_request failed in auto_join path")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Login failed, please try again",
        ) from exc

    emit_event(
        "login",
        user_id=zitadel_user_id,
        properties={"method": "idp", "auto_join": True, "org_id": org_id},
    )
    logger.info("workspace_auto_joined", zitadel_user_id=zitadel_user_id, org_id=org_id)
    return SelectWorkspaceAutoJoin(kind="auto_join", workspace_url=workspace_url)


async def _handle_join_request(
    *,
    email: str,
    zitadel_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> SelectWorkspacePending:
    """C4.4: INSERT portal_join_requests + notify admins. No SSO cookie."""
    from datetime import datetime, timedelta

    expires_at = datetime.now(UTC) + timedelta(days=7)

    # placeholder token -- updated after flush (same pattern as auth_join.py)
    join_request = PortalJoinRequest(
        zitadel_user_id=zitadel_user_id,
        email=email,
        org_id=org_id,
        status="pending",
        approval_token="placeholder",  # noqa: S106
        expires_at=expires_at,
    )
    db.add(join_request)
    await db.flush()
    # Generate deterministic HMAC token using the assigned row id
    join_request.approval_token = generate_approval_token(join_request.id, zitadel_user_id)

    # Notify admins (non-blocking) -- look up admin emails
    try:
        result = await db.execute(
            select(PortalUser).where(
                PortalUser.org_id == org_id,
                PortalUser.role.in_(["admin", "group-admin"]),
                PortalUser.status == "active",
            )
        )
        admins = result.scalars().all()
        for admin in admins:
            if admin.email:
                await notify_admin_join_request(
                    email=email,
                    display_name=email,
                    org_id=org_id,
                    admin_email=admin.email,
                )
    except Exception:
        logger.warning("notify_join_request_admins_failed", org_id=org_id, exc_info=True)

    logger.info("workspace_join_request_created", zitadel_user_id=zitadel_user_id, org_id=org_id)
    return SelectWorkspacePending(kind="join_request_pending", redirect_to="/join-request/sent")
