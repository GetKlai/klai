"""Widget-to-HubSpot handoff routes: start a handoff, send a message, stream its events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.partner_dependencies import (
    PartnerAuthContext,
    get_partner_key,
)
from app.api.widget_public import WIDGET_PUBLIC_CORS
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.models.portal import PortalOrg
from app.models.widgets import Widget
from app.services.redis_client import get_redis_pool
from app.services.widget_auth import origin_allowed
from app.services.widget_handoff import (
    get_active_handoff_session_id,
    list_visible_handoff_messages,
    send_handoff_visitor_message,
    start_hubspot_handoff,
)

logger = structlog.get_logger()

router = APIRouter()


class HandoffTranscriptMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=4000)


class StartHubSpotHandoffRequest(BaseModel):
    summary: str | None = Field(default=None, max_length=4000)
    visitor_name: str | None = Field(default=None, max_length=120)
    visitor_email: str | None = Field(default=None, max_length=254)
    messages: list[HandoffTranscriptMessage] = Field(default_factory=list, max_length=50)


class SendHubSpotHandoffMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    visitor_name: str | None = Field(default=None, max_length=120)


class HubSpotHandoffResponse(BaseModel):
    id: int
    status: str
    integration_thread_id: str
    hubspot_conversations_thread_id: str | None = None


class HubSpotHandoffMessageResponse(BaseModel):
    id: int | None
    handoff_session_id: int
    hubspot_message_id: str | None = None


# ---------------------------------------------------------------------------
# Widget human handoff endpoints
# ---------------------------------------------------------------------------


def _require_widget_auth(auth: PartnerAuthContext) -> None:
    if not str(auth.key_id).startswith("wgt_") or not auth.session_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"type": "permission_error", "message": "Widget session required"}},
        )


def _request_origin(request: Request | None) -> str | None:
    headers = getattr(request, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    value = headers.get("origin") or headers.get("Origin")
    return value if isinstance(value, str) and value else None


def _hubspot_handoff_forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"type": "permission_error", "message": "HubSpot handoff is not enabled for this widget"}},
    )


async def _require_hubspot_widget_handoff_enabled(
    *,
    db: AsyncSession,
    auth: PartnerAuthContext,
    request: Request | None,
) -> None:
    _require_widget_auth(auth)
    result = await db.execute(
        select(Widget, PortalOrg)
        .join(PortalOrg, PortalOrg.id == Widget.org_id)
        .where(
            Widget.widget_id == str(auth.key_id),
            Widget.org_id == auth.org_id,
            Widget.deleted_at.is_(None),
        )
    )
    row = result.first()
    if row is None:
        raise _hubspot_handoff_forbidden()

    widget_row = row[0]
    org = row[1]
    widget_config_data = widget_row.widget_config or {}
    # Origin is checked against THIS widget's own allowlist, not against one
    # hardcoded pilot host. Same security property — a handoff can only be
    # driven from a page the widget is allowed to run on — but it now holds for
    # every tenant instead of one.
    # A missing Origin is refused, matching widget_config and the beacon
    # endpoint below. The old hardcoded comparison rejected it as a side effect
    # of not matching the pilot host; that was worth keeping deliberately. A
    # browser always sends Origin on the cross-origin POST the widget makes, so
    # an absent header means the caller is not the widget.
    request_origin = _request_origin(request)
    if request_origin is None or not origin_allowed(
        request_origin,
        widget_config_data.get("allowed_origins") or [],
        allow_any_origin=bool(widget_row.allow_any_origin),
    ):
        raise _hubspot_handoff_forbidden()
    if not _hubspot_handoff_enabled_for_widget(org=org, widget_config_data=widget_config_data):
        raise _hubspot_handoff_forbidden()


@router.post("/widget-handoffs/hubspot/start", response_model=HubSpotHandoffResponse, openapi_extra=WIDGET_PUBLIC_CORS)
async def start_widget_hubspot_handoff(
    http_request: Request,
    request: StartHubSpotHandoffRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> HubSpotHandoffResponse:
    await _require_hubspot_widget_handoff_enabled(db=db, auth=auth, request=http_request)
    try:
        result = await start_hubspot_handoff(
            db,
            org_id=auth.org_id,
            widget_public_id=str(auth.key_id),
            session_key=auth.session_key or "",
            summary=request.summary,
            visitor_name=request.visitor_name,
            visitor_email=request.visitor_email,
            messages=[message.model_dump() for message in request.messages],
        )
    except Exception as exc:
        logger.exception("widget_hubspot_handoff_start_failed", org_id=auth.org_id, widget_id=str(auth.key_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "handoff_error", "message": "Could not start HubSpot handoff"}},
        ) from exc
    return HubSpotHandoffResponse(**result)


@router.post(
    "/widget-handoffs/hubspot/messages",
    response_model=HubSpotHandoffMessageResponse,
    openapi_extra=WIDGET_PUBLIC_CORS,
)
async def send_widget_hubspot_handoff_message(
    http_request: Request,
    request: SendHubSpotHandoffMessageRequest,
    auth: PartnerAuthContext = Depends(get_partner_key),
    db: AsyncSession = Depends(get_db),
) -> HubSpotHandoffMessageResponse:
    await _require_hubspot_widget_handoff_enabled(db=db, auth=auth, request=http_request)
    try:
        result = await send_handoff_visitor_message(
            db,
            org_id=auth.org_id,
            widget_public_id=str(auth.key_id),
            session_key=auth.session_key or "",
            content=request.content,
            visitor_name=request.visitor_name,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"type": "handoff_error", "message": "No active HubSpot handoff"}},
        ) from exc
    except Exception as exc:
        logger.exception("widget_hubspot_handoff_message_failed", org_id=auth.org_id, widget_id=str(auth.key_id))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "handoff_error", "message": "Could not send HubSpot handoff message"}},
        ) from exc
    return HubSpotHandoffMessageResponse(**result)


@router.get("/widget-handoffs/hubspot/events", openapi_extra=WIDGET_PUBLIC_CORS)
async def stream_widget_hubspot_handoff_events(
    request: Request,
    last_event_id: int = 0,
) -> StreamingResponse:
    async with AsyncSessionLocal() as db:
        auth = await get_partner_key(request=request, db=db)
        await _require_hubspot_widget_handoff_enabled(db=db, auth=auth, request=request)
        handoff_session_id = await get_active_handoff_session_id(
            db,
            org_id=auth.org_id,
            widget_public_id=str(auth.key_id),
            session_key=auth.session_key or "",
        )
        if handoff_session_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"type": "handoff_error", "message": "No active HubSpot handoff"}},
            )

    channel = f"widget_handoff:{handoff_session_id}"

    async def _events() -> AsyncGenerator[bytes]:
        seen_id = last_event_id
        redis = await get_redis_pool()
        pubsub: Any | None = None
        try:
            if redis is not None:
                pubsub = redis.pubsub()
                await pubsub.subscribe(channel)

            async with AsyncSessionLocal() as replay_db:
                replay_messages = await list_visible_handoff_messages(
                    replay_db,
                    handoff_session_id=handoff_session_id,
                    after_id=last_event_id,
                )

            for message in replay_messages:
                seen_id = max(seen_id, int(message["id"]))
                yield f"id: {message['id']}\nevent: message\ndata: {json.dumps(message)}\n\n".encode()

            if pubsub is None:
                while True:
                    await asyncio.sleep(15)
                    yield b": heartbeat\n\n"

            assert pubsub is not None
            while True:
                event = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if event is None:
                    yield b": heartbeat\n\n"
                    continue
                raw_data = event.get("data")
                if not isinstance(raw_data, str):
                    continue
                try:
                    payload = json.loads(raw_data)
                except json.JSONDecodeError:
                    continue
                event_id = int(payload.get("id") or 0)
                if event_id and event_id <= seen_id:
                    continue
                if event_id:
                    seen_id = event_id
                yield f"id: {event_id}\nevent: message\ndata: {json.dumps(payload)}\n\n".encode()
        finally:
            if pubsub is not None:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()

    return StreamingResponse(_events(), media_type="text/event-stream")


def _hubspot_handoff_enabled_for_widget(
    *,
    org: PortalOrg,
    widget_config_data: dict[str, Any],
) -> bool:
    """Whether this widget may hand a visitor over to HubSpot.

    Two conditions, and the first one is not a leftover.

    There is exactly ONE HubSpot destination on the platform:
    ``start_hubspot_handoff`` calls ``ensure_channel_account()`` with no
    argument and publishes to ``settings.hubspot_webchat_delivery_identifier``,
    a single global recipient. Until a per-tenant channel binding exists, any
    tenant allowed through here publishes its visitors' transcripts and contact
    details into that one shared inbox. So the org gate stays — but keyed on
    ``settings.platform_org_slug``, the same source of truth as
    ``_assert_internal_hubspot_allowed`` on the connect endpoints, instead of
    the tenant name that used to be compiled in here.

    The second condition, the widget's own connected channel, is necessary but
    NOT sufficient: ``integrations`` is client-writable through the generic
    widget PATCH, so an admin can set ``status: "connected"`` without ever
    completing the protected connect flow.
    """
    if org.slug != settings.platform_org_slug:
        return False
    integrations = widget_config_data.get("integrations")
    if not isinstance(integrations, dict):
        return False
    hubspot = integrations.get("hubspot")
    return bool(
        isinstance(hubspot, dict) and hubspot.get("status") == "connected" and hubspot.get("channel_account_id")
    )
