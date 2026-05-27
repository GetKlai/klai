"""Admin Widget management endpoints — SPEC-WIDGET-002.

CRUD for chat widgets scoped to the caller's org. Auth: Zitadel OIDC
session with admin/owner role check.

Widgets are a first-class domain separate from partner API keys:
- No authentication-secret columns (no key_prefix, key_hash, permissions).
  Widget auth is 100% JWT-based via WIDGET_JWT_SECRET.
- KB access is read-only (no access_level column in widget_kb_access).
- No `active` / revoke action — DELETE is the only way to end a widget.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least, require_platform_unlocked
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.models.widgets import Widget, WidgetKbAccess, generate_widget_id
from app.services.events import emit_event
from app.services.hubspot_custom_channel import (
    HubSpotAPIError,
    HubSpotChannelAccount,
    HubSpotNotConfiguredError,
    ensure_channel_account,
    hubspot_webchat_configured,
    send_test_message,
    set_channel_account_authorized,
)
from app.services.widget_auth import generate_session_token

logger = structlog.get_logger()

router = APIRouter(prefix="/api/admin/widgets", tags=["Widgets Admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class HubSpotWidgetIntegration(BaseModel):
    status: Literal["not_connected", "connected", "disconnected", "error"] = "not_connected"
    portal_id: str | None = None
    channel_id: str | None = None
    channel_account_id: str | None = None
    inbox_id: str | None = None
    help_desk_url: str | None = None
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    last_rebuilt_at: str | None = None
    last_tested_at: str | None = None
    last_test_thread_id: str | None = None
    last_error: str | None = None


class WidgetIntegrations(BaseModel):
    hubspot: HubSpotWidgetIntegration = Field(default_factory=HubSpotWidgetIntegration)


class WidgetConfig(BaseModel):
    allowed_origins: list[str] = Field(default_factory=list)
    title: str = ""
    welcome_message: str = ""
    system_prompt: str = Field(default="", max_length=4000)
    css_variables: dict[str, str] = Field(default_factory=dict)
    # TWD-style starter chips shown on the empty state. Max 6 per
    # TalkWithData convention. Each entry is rendered as a clickable
    # pill that submits the text as the first user message.
    conversation_starters: list[str] = Field(default_factory=list, max_length=6)
    # When true, the widget hides the "AI-antwoorden kunnen fouten
    # bevatten…" footer (white-label / power-user flag, matches the
    # "Verberg 'Powered by'" pattern in the TWD editor).
    hide_disclaimer: bool = False
    # Optional reference to a Template (app/templates) — when set, the
    # template's prompt_text is appended to system_prompt at runtime so
    # admins can re-use named prompts across widgets without copy-paste.
    template_slug: str | None = None
    # TWD-parity appearance & chat-display fields. Stored on
    # widget_config; widget client consumes those it knows. Unknown
    # fields are tolerated (Pydantic-validated, but no runtime effect
    # until the widget client adds rendering for each one).
    primary_color: str = "#fcaa2d"
    theme: str = "light"  # 'light' | 'dark'
    show_sources: bool = True
    show_meta: bool = False
    collect_user_info: bool = False
    page_context_enabled: bool = False
    widget_position: str = "right"  # 'left' | 'right'
    integrations: WidgetIntegrations = Field(default_factory=lambda: WidgetIntegrations())


class CreateWidgetRequest(BaseModel):
    name: str = Field(min_length=3, max_length=128)
    description: str | None = None
    kb_ids: list[int] = Field(default_factory=list)
    rate_limit_rpm: int = Field(default=60, ge=10, le=600)
    widget_config: WidgetConfig | None = None
    public_share_enabled: bool = False
    # REQ-2 (Finding B-2): explicit opt-in for open-world origin policy.
    # When False (default), allowed_origins is auto-filled with the tenant subdomain.
    allow_any_origin: bool = False


class WidgetResponse(BaseModel):
    id: str
    name: str
    description: str | None
    widget_id: str
    widget_config: WidgetConfig
    public_share_enabled: bool
    # REQ-2 (Finding B-2): expose allow_any_origin flag so the UI toggle can bind to it.
    allow_any_origin: bool
    kb_access_count: int
    rate_limit_rpm: int
    last_used_at: str | None
    created_at: str
    created_by: str


class WidgetDetailResponse(WidgetResponse):
    kb_access: list[dict]  # [{kb_id, kb_name, kb_slug}]


class UpdateWidgetRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    kb_ids: list[int] | None = None
    rate_limit_rpm: int | None = None
    widget_config: WidgetConfig | None = None
    public_share_enabled: bool | None = None
    # REQ-2 (Finding B-2): allow toggling the allow_any_origin flag via update.
    allow_any_origin: bool | None = None


class HubSpotIntegrationStatusResponse(BaseModel):
    configured: bool
    status: Literal["not_configured", "not_connected", "connected", "disconnected", "error"]
    portal_id: str | None = None
    channel_id: str | None = None
    channel_account_id: str | None = None
    inbox_id: str | None = None
    help_desk_url: str | None = None
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    last_rebuilt_at: str | None = None
    last_tested_at: str | None = None
    last_test_thread_id: str | None = None
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _widget_to_response(widget: Widget, kb_access_count: int) -> WidgetResponse:
    config = widget.widget_config or {}
    return WidgetResponse(
        id=widget.id,
        name=widget.name,
        description=widget.description,
        widget_id=widget.widget_id,
        widget_config=WidgetConfig(
            allowed_origins=config.get("allowed_origins", []),
            title=config.get("title", ""),
            welcome_message=config.get("welcome_message", ""),
            system_prompt=config.get("system_prompt", ""),
            css_variables=config.get("css_variables", {}),
            conversation_starters=config.get("conversation_starters", []),
            hide_disclaimer=config.get("hide_disclaimer", False),
            template_slug=config.get("template_slug"),
            primary_color=config.get("primary_color", "#fcaa2d"),
            theme=config.get("theme", "light"),
            show_sources=config.get("show_sources", True),
            show_meta=config.get("show_meta", False),
            collect_user_info=config.get("collect_user_info", False),
            page_context_enabled=config.get("page_context_enabled", False),
            widget_position=config.get("widget_position", "right"),
            integrations=config.get("integrations", {}),
        ),
        public_share_enabled=widget.public_share_enabled,
        allow_any_origin=getattr(widget, "allow_any_origin", False),
        kb_access_count=kb_access_count,
        rate_limit_rpm=widget.rate_limit_rpm,
        last_used_at=str(widget.last_used_at) if widget.last_used_at else None,
        created_at=str(widget.created_at),
        created_by=widget.created_by,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _assert_internal_hubspot_allowed(perms: UserPermissions) -> None:
    if perms.org_slug != settings.platform_org_slug:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Integration not found")


def _hubspot_from_widget(widget: Widget) -> HubSpotWidgetIntegration:
    config = widget.widget_config or {}
    integrations = config.get("integrations") if isinstance(config.get("integrations"), dict) else {}
    hubspot = integrations.get("hubspot") if isinstance(integrations, dict) else {}
    if not isinstance(hubspot, dict):
        hubspot = {}
    return HubSpotWidgetIntegration(**hubspot)


def _store_hubspot_on_widget(widget: Widget, hubspot: HubSpotWidgetIntegration) -> None:
    config = dict(widget.widget_config or {})
    integrations = dict(config.get("integrations") or {})
    integrations["hubspot"] = hubspot.model_dump()
    config["integrations"] = integrations
    widget.widget_config = config


def _hubspot_status_response(hubspot: HubSpotWidgetIntegration) -> HubSpotIntegrationStatusResponse:
    configured = hubspot_webchat_configured()
    status_value: Literal["not_configured", "not_connected", "connected", "disconnected", "error"]
    if not configured:
        status_value = "not_configured"
    else:
        status_value = hubspot.status
    return HubSpotIntegrationStatusResponse(
        configured=configured,
        status=status_value,
        portal_id=hubspot.portal_id or settings.hubspot_webchat_portal_id or None,
        channel_id=hubspot.channel_id or settings.hubspot_webchat_custom_channel_id or None,
        channel_account_id=hubspot.channel_account_id,
        inbox_id=hubspot.inbox_id or settings.hubspot_webchat_inbox_id or None,
        help_desk_url=hubspot.help_desk_url or settings.hubspot_webchat_help_desk_url or None,
        last_connected_at=hubspot.last_connected_at,
        last_disconnected_at=hubspot.last_disconnected_at,
        last_rebuilt_at=hubspot.last_rebuilt_at,
        last_tested_at=hubspot.last_tested_at,
        last_test_thread_id=hubspot.last_test_thread_id,
        last_error=hubspot.last_error,
    )


def _connected_hubspot_from_account(
    account: HubSpotChannelAccount,
    previous: HubSpotWidgetIntegration,
    *,
    connected_at: str | None = None,
    rebuilt_at: str | None = None,
) -> HubSpotWidgetIntegration:
    return previous.model_copy(
        update={
            "status": "connected",
            "portal_id": settings.hubspot_webchat_portal_id,
            "channel_id": account.channel_id,
            "channel_account_id": account.id,
            "inbox_id": account.inbox_id,
            "help_desk_url": settings.hubspot_webchat_help_desk_url,
            "last_connected_at": connected_at or previous.last_connected_at or _now_iso(),
            "last_rebuilt_at": rebuilt_at or previous.last_rebuilt_at,
            "last_error": None,
        }
    )


def _hubspot_http_error(exc: HubSpotAPIError | HubSpotNotConfiguredError) -> HTTPException:
    if isinstance(exc, HubSpotNotConfiguredError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


async def _get_widget_or_404(
    widget_id: str,
    org_id: int,
    db: AsyncSession,
    *,
    include_deleted: bool = False,
) -> Widget:
    """Tenant-scoped widget lookup.

    REQ-16 (Finding B-14, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): callers that
    drive live widget behaviour MUST exclude soft-deleted widgets (default).
    Audit-trail endpoints that read historical conversations pass
    ``include_deleted=True`` so admins keep being able to investigate after
    a widget is wiped.
    """
    conditions = [Widget.id == widget_id, Widget.org_id == org_id]
    if not include_deleted:
        conditions.append(Widget.deleted_at.is_(None))
    result = await db.execute(select(Widget).where(*conditions))
    widget = result.scalar_one_or_none()
    if widget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Widget not found")
    return widget


async def _validate_kb_ids(kb_ids: list[int], org_id: int, db: AsyncSession) -> list[PortalKnowledgeBase]:
    if not kb_ids:
        return []
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id.in_(kb_ids),
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    found_kbs = result.scalars().all()
    found_ids = {kb.id for kb in found_kbs}
    missing = set(kb_ids) - found_ids
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge base IDs not found in your organisation: {sorted(missing)}",
        )
    return list(found_kbs)


async def _count_kb_access(widget_id: str, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget_id)
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# POST /api/widgets
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_widget(
    body: CreateWidgetRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> WidgetDetailResponse:
    """Create a new chat widget."""
    await _validate_kb_ids(body.kb_ids, perms.org_id, db)

    widget_id_str = generate_widget_id()
    internal_id = str(uuid.uuid4())
    config = (body.widget_config or WidgetConfig()).model_dump()

    # REQ-2 (Finding B-2): when allow_any_origin=False and no allowed_origins
    # provided, auto-fill the tenant subdomain so the widget is locked to the
    # org's own portal domain instead of denying all traffic on first use.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
    if not body.allow_any_origin and not config.get("allowed_origins"):
        config["allowed_origins"] = [f"https://{perms.org_slug}.getklai.com"]

    widget_row = Widget(
        id=internal_id,
        org_id=perms.org_id,
        name=body.name,
        description=body.description,
        widget_id=widget_id_str,
        widget_config=config,
        public_share_enabled=body.public_share_enabled,
        allow_any_origin=body.allow_any_origin,
        rate_limit_rpm=body.rate_limit_rpm,
        created_by=perms.user_id,
    )
    db.add(widget_row)

    for kb_id in body.kb_ids:
        db.add(WidgetKbAccess(widget_id=internal_id, kb_id=kb_id))

    await db.flush()  # Promote widget_row to persistent so refresh() can run.
    await db.refresh(widget_row)  # Pre-commit refresh to load server_default columns while tenant context is still set.

    # Load KB names for the response BEFORE commit. PortalKnowledgeBase is
    # RLS-protected — after commit, the transaction-scoped tenant GUC is
    # cleared and the next query raises InsufficientPrivilegeError "RLS:
    # app.current_org_id is not set" (2026-05-20 incident on Nerds widget
    # create returned 500 even though the widget itself persisted).
    kb_access_list: list[dict] = []
    if body.kb_ids:
        kb_result = await db.execute(select(PortalKnowledgeBase).where(PortalKnowledgeBase.id.in_(body.kb_ids)))
        kbs = {kb.id: kb for kb in kb_result.scalars().all()}
        kb_access_list = [
            {"kb_id": kb_id, "kb_name": kbs[kb_id].name, "kb_slug": kbs[kb_id].slug}
            for kb_id in body.kb_ids
            if kb_id in kbs
        ]

    await db.commit()

    emit_event(
        "widget.created",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": internal_id, "widget_public_id": widget_id_str, "name": body.name},
    )
    logger.info("Widget created", widget_id=internal_id, public_id=widget_id_str, org_id=perms.org_id)

    response = _widget_to_response(widget_row, len(body.kb_ids))
    return WidgetDetailResponse(**response.model_dump(), kb_access=kb_access_list)


# ---------------------------------------------------------------------------
# GET /api/widgets
# ---------------------------------------------------------------------------


@router.get("")
async def list_widgets(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> list[WidgetResponse]:
    """List all widgets for the caller's org."""
    result = await db.execute(select(Widget).where(Widget.org_id == perms.org_id, Widget.deleted_at.is_(None)))
    widgets = result.scalars().all()
    if not widgets:
        return []

    widget_ids = [w.id for w in widgets]
    count_result = await db.execute(
        select(
            WidgetKbAccess.widget_id,
            func.count().label("cnt"),
        )
        .where(WidgetKbAccess.widget_id.in_(widget_ids))
        .group_by(WidgetKbAccess.widget_id)
    )
    kb_counts = {row.widget_id: row.cnt for row in count_result}

    return [_widget_to_response(w, kb_counts.get(w.id, 0)) for w in widgets]


# ---------------------------------------------------------------------------
# GET /api/widgets/{id}
# ---------------------------------------------------------------------------


@router.get("/{widget_id}")
async def get_widget_detail(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> WidgetDetailResponse:
    """Get full detail for a single widget."""
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)

    kb_result = await db.execute(
        select(WidgetKbAccess, PortalKnowledgeBase)
        .join(PortalKnowledgeBase, WidgetKbAccess.kb_id == PortalKnowledgeBase.id)
        .where(WidgetKbAccess.widget_id == widget.id)
    )
    kb_access_list = [
        {
            "kb_id": access.kb_id,
            "kb_name": kb.name,
            "kb_slug": kb.slug,
        }
        for access, kb in kb_result
    ]

    response = _widget_to_response(widget, len(kb_access_list))
    return WidgetDetailResponse(**response.model_dump(), kb_access=kb_access_list)


# ---------------------------------------------------------------------------
# GET /api/admin/widgets/{id}/preview-session
# ---------------------------------------------------------------------------


class PreviewSessionResponse(BaseModel):
    session_token: str
    chat_endpoint: str
    session_expires_at: str


@router.get("/{widget_id}/preview-session", response_model=PreviewSessionResponse)
async def widget_preview_session(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> PreviewSessionResponse:
    """Issue a short-lived session token for the admin's own widget,
    no Origin check. Powers the test page chat without touching
    allowed_origins. Auth is the admin's portal cookie + ownership.
    """
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
    org = await db.get(PortalOrg, perms.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Org not found")
    if not settings.widget_jwt_secret:
        raise HTTPException(status_code=503, detail="Widget auth not configured")

    kb_rows = await db.execute(select(WidgetKbAccess.kb_id).where(WidgetKbAccess.widget_id == widget.id))
    kb_ids = [row[0] for row in kb_rows.all()]

    # REQ-15 (Finding B-11): mark the admin-preview JWT with is_preview=true
    # so widget_audit can flag the conversation and the stats query can
    # exclude it. @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-15
    token = generate_session_token(
        wgt_id=widget.widget_id,
        org_id=widget.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
        is_preview=True,
    )
    return PreviewSessionResponse(
        session_token=token,
        chat_endpoint="/partner/v1/chat/completions",
        session_expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


# ---------------------------------------------------------------------------
# HubSpot integration lifecycle (internal getklai org only)
# ---------------------------------------------------------------------------


@router.get("/{widget_id}/integrations/hubspot", response_model=HubSpotIntegrationStatusResponse)
async def get_hubspot_integration_status(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> HubSpotIntegrationStatusResponse:
    _assert_internal_hubspot_allowed(perms)
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
    return _hubspot_status_response(_hubspot_from_widget(widget))


@router.post("/{widget_id}/integrations/hubspot/connect", response_model=HubSpotIntegrationStatusResponse)
async def connect_hubspot_integration(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> HubSpotIntegrationStatusResponse:
    _assert_internal_hubspot_allowed(perms)
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
    previous = _hubspot_from_widget(widget)
    try:
        account = await ensure_channel_account(previous.channel_account_id)
    except (HubSpotAPIError, HubSpotNotConfiguredError) as exc:
        failed = previous.model_copy(update={"status": "error", "last_error": str(exc)})
        _store_hubspot_on_widget(widget, failed)
        await db.commit()
        raise _hubspot_http_error(exc) from exc

    next_state = _connected_hubspot_from_account(account, previous, connected_at=_now_iso())
    _store_hubspot_on_widget(widget, next_state)
    await db.commit()
    emit_event(
        "widget.hubspot_connected",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": widget.id, "channel_account_id": account.id},
    )
    return _hubspot_status_response(next_state)


@router.post("/{widget_id}/integrations/hubspot/disconnect", response_model=HubSpotIntegrationStatusResponse)
async def disconnect_hubspot_integration(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> HubSpotIntegrationStatusResponse:
    _assert_internal_hubspot_allowed(perms)
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
    previous = _hubspot_from_widget(widget)
    if previous.channel_account_id:
        try:
            await set_channel_account_authorized(previous.channel_account_id, authorized=False)
        except (HubSpotAPIError, HubSpotNotConfiguredError) as exc:
            failed = previous.model_copy(update={"status": "error", "last_error": str(exc)})
            _store_hubspot_on_widget(widget, failed)
            await db.commit()
            raise _hubspot_http_error(exc) from exc

    next_state = previous.model_copy(
        update={
            "status": "disconnected",
            "last_disconnected_at": _now_iso(),
            "last_error": None,
        }
    )
    _store_hubspot_on_widget(widget, next_state)
    await db.commit()
    emit_event(
        "widget.hubspot_disconnected",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": widget.id, "channel_account_id": previous.channel_account_id},
    )
    return _hubspot_status_response(next_state)


@router.post("/{widget_id}/integrations/hubspot/rebuild", response_model=HubSpotIntegrationStatusResponse)
async def rebuild_hubspot_integration(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> HubSpotIntegrationStatusResponse:
    _assert_internal_hubspot_allowed(perms)
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
    previous = _hubspot_from_widget(widget)
    try:
        account = await ensure_channel_account(previous.channel_account_id)
    except (HubSpotAPIError, HubSpotNotConfiguredError) as exc:
        failed = previous.model_copy(update={"status": "error", "last_error": str(exc)})
        _store_hubspot_on_widget(widget, failed)
        await db.commit()
        raise _hubspot_http_error(exc) from exc

    next_state = _connected_hubspot_from_account(account, previous, rebuilt_at=_now_iso())
    _store_hubspot_on_widget(widget, next_state)
    await db.commit()
    emit_event(
        "widget.hubspot_rebuilt",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": widget.id, "channel_account_id": account.id},
    )
    return _hubspot_status_response(next_state)


@router.post("/{widget_id}/integrations/hubspot/test-message", response_model=HubSpotIntegrationStatusResponse)
async def test_hubspot_integration(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> HubSpotIntegrationStatusResponse:
    _assert_internal_hubspot_allowed(perms)
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
    previous = _hubspot_from_widget(widget)
    if not previous.channel_account_id or previous.status != "connected":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="HubSpot is not connected")
    try:
        message = await send_test_message(
            previous.channel_account_id,
            widget_name=widget.name,
            widget_public_id=widget.widget_id,
        )
    except (HubSpotAPIError, HubSpotNotConfiguredError) as exc:
        failed = previous.model_copy(update={"status": "error", "last_error": str(exc)})
        _store_hubspot_on_widget(widget, failed)
        await db.commit()
        raise _hubspot_http_error(exc) from exc

    next_state = previous.model_copy(
        update={
            "status": "connected",
            "last_tested_at": _now_iso(),
            "last_test_thread_id": str(message.get("conversationsThreadId") or ""),
            "last_error": None,
        }
    )
    _store_hubspot_on_widget(widget, next_state)
    await db.commit()
    emit_event(
        "widget.hubspot_test_message_sent",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={
            "widget_id": widget.id,
            "channel_account_id": previous.channel_account_id,
            "conversations_thread_id": next_state.last_test_thread_id,
        },
    )
    return _hubspot_status_response(next_state)


# ---------------------------------------------------------------------------
# PATCH /api/widgets/{id}
# ---------------------------------------------------------------------------


@router.patch("/{widget_id}")
async def update_widget(
    widget_id: str,
    body: UpdateWidgetRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> WidgetResponse:
    """Partial update of a widget."""
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)

    if body.name is not None:
        widget.name = body.name
    if body.description is not None:
        widget.description = body.description
    if body.rate_limit_rpm is not None:
        widget.rate_limit_rpm = body.rate_limit_rpm
    if body.widget_config is not None:
        widget.widget_config = body.widget_config.model_dump()
    if body.public_share_enabled is not None:
        widget.public_share_enabled = body.public_share_enabled
    # REQ-2 (Finding B-2): allow toggling the allow_any_origin flag via PATCH.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
    if body.allow_any_origin is not None:
        widget.allow_any_origin = body.allow_any_origin

    if body.kb_ids is not None:
        await _validate_kb_ids(body.kb_ids, perms.org_id, db)
        await db.execute(delete(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget.id))
        for kb_id in body.kb_ids:
            db.add(WidgetKbAccess(widget_id=widget.id, kb_id=kb_id))

    await db.commit()

    kb_access_count = len(body.kb_ids) if body.kb_ids is not None else await _count_kb_access(widget.id, db)

    emit_event(
        "widget.updated",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": widget.id, "name": widget.name},
    )

    return _widget_to_response(widget, kb_access_count)


# ---------------------------------------------------------------------------
# DELETE /api/widgets/{id}
# ---------------------------------------------------------------------------


@router.delete("/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    widget_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a widget and revoke its KB access entries.

    REQ-16 (Finding B-14, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): widgets are
    soft-deleted (``deleted_at = NOW()``) so the conversation/messages audit
    trail survives admin "wipe traces" attempts. ``widget_kb_access`` is
    still hard-deleted so a soft-deleted widget cannot route to any KB.
    @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-16
    """
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)

    # Revoke KB access on soft-delete: a soft-deleted widget MUST NOT keep
    # querying KBs. The audit-trail rows that DO survive (conversations,
    # messages) only reference the widget id, never the KB list.
    await db.execute(delete(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget.id))
    widget.deleted_at = datetime.now(UTC)
    await db.commit()

    emit_event(
        "widget.deleted",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": widget.id, "name": widget.name},
    )
    logger.info("Widget soft-deleted", widget_id=widget.id, org_id=perms.org_id)


# ---------------------------------------------------------------------------
# Audit-trail endpoints — SPEC-WIDGET-ACTIVITY-001
# ---------------------------------------------------------------------------


class ConversationListItem(BaseModel):
    id: int
    started_at: datetime
    last_message_at: datetime
    message_count: int
    first_user_query: str | None
    language_detected: str | None


class WidgetMessageItem(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    sources: list[dict] | None
    created_at: datetime
    sequence: int


class ConversationDetail(ConversationListItem):
    messages: list[WidgetMessageItem]


class TopQuery(BaseModel):
    query: str
    count: int


class WidgetStats(BaseModel):
    period: Literal["7d", "30d", "all"]
    total_conversations: int
    total_messages: int
    avg_messages_per_conversation: float
    top_queries: list[TopQuery]
    # 24 buckets, hour-of-day. Aggregated across all days in window.
    hourly_activity: list[int]


def _period_cutoff(period: str) -> datetime | None:
    """Translate period string to a SQL ``started_at >= cutoff`` value.

    ``all`` returns None so the caller skips the time filter."""
    now = datetime.now(UTC)
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    return None


@router.get("/{widget_id}/conversations", response_model=list[ConversationListItem])
async def list_widget_conversations(
    widget_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationListItem]:
    """Paginated list of conversations for one widget, newest first.

    Cursor = ISO timestamp of the last row returned. Pass it to
    ``cursor`` to fetch the next page (``started_at < cursor``).

    REQ-16: audit-trail endpoints accept soft-deleted widgets so admins
    keep being able to read conversation history after a widget is wiped.
    """
    widget = await _get_widget_or_404(widget_id, perms.org_id, db, include_deleted=True)

    params: dict[str, object] = {"widget_id": widget.id, "limit": limit}
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            params["cursor"] = cursor_dt
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc

    if cursor:
        result = await db.execute(
            text(
                "SELECT id, started_at, last_message_at, message_count, "
                "first_user_query, language_detected "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND started_at < :cursor "
                "ORDER BY started_at DESC LIMIT :limit"
            ),
            params,
        )
    else:
        result = await db.execute(
            text(
                "SELECT id, started_at, last_message_at, message_count, "
                "first_user_query, language_detected "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "ORDER BY started_at DESC LIMIT :limit"
            ),
            params,
        )
    rows = result.all()
    return [
        ConversationListItem(
            id=row.id,
            started_at=row.started_at,
            last_message_at=row.last_message_at,
            message_count=row.message_count,
            first_user_query=row.first_user_query,
            language_detected=row.language_detected,
        )
        for row in rows
    ]


@router.get("/{widget_id}/conversations/{conv_id}", response_model=ConversationDetail)
async def get_widget_conversation(
    widget_id: str,
    conv_id: int,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    """Full transcript of one conversation, messages in chronological order.

    REQ-16: audit-trail endpoints accept soft-deleted widgets.
    """
    widget = await _get_widget_or_404(widget_id, perms.org_id, db, include_deleted=True)

    conv_result = await db.execute(
        text(
            """
            SELECT id, started_at, last_message_at, message_count,
                   first_user_query, language_detected
              FROM widget_conversations
             WHERE id = :conv_id
               AND widget_id = CAST(:widget_id AS uuid)
            """
        ),
        {"conv_id": conv_id, "widget_id": widget.id},
    )
    conv_row = conv_result.first()
    if conv_row is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    msg_result = await db.execute(
        text(
            """
            SELECT id, role, content, sources, created_at, sequence
              FROM widget_messages
             WHERE conversation_id = :conv_id
             ORDER BY sequence ASC
            """
        ),
        {"conv_id": conv_id},
    )
    messages = [
        WidgetMessageItem(
            id=m.id,
            role=m.role,  # type: ignore[arg-type]
            content=m.content,
            sources=m.sources,
            created_at=m.created_at,
            sequence=m.sequence,
        )
        for m in msg_result.all()
    ]

    return ConversationDetail(
        id=conv_row.id,
        started_at=conv_row.started_at,
        last_message_at=conv_row.last_message_at,
        message_count=conv_row.message_count,
        first_user_query=conv_row.first_user_query,
        language_detected=conv_row.language_detected,
        messages=messages,
    )


@router.get("/{widget_id}/stats", response_model=WidgetStats)
async def widget_activity_stats(
    widget_id: str,
    period: Literal["7d", "30d", "all"] = "7d",
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("widgets")),
    db: AsyncSession = Depends(get_db),
) -> WidgetStats:
    """Aggregate metrics for the Activiteit tab.

    Three queries: totals, top 10 first-user-queries, and 24 hourly
    buckets. The ``period`` filter scopes everything to a rolling
    window of 7 / 30 days, or all-time.

    REQ-16: audit-trail endpoints accept soft-deleted widgets so the
    admin Activity tab keeps surfacing history after a widget is wiped.
    """
    widget = await _get_widget_or_404(widget_id, perms.org_id, db, include_deleted=True)
    cutoff = _period_cutoff(period)

    params: dict[str, object] = {"widget_id": widget.id}
    if cutoff is not None:
        params["cutoff"] = cutoff

    if cutoff is not None:
        totals_result = await db.execute(
            text(
                "SELECT COUNT(*) AS total_conversations, "
                "COALESCE(SUM(message_count), 0) AS total_messages "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND is_preview = false "
                "AND started_at >= :cutoff"
            ),
            params,
        )
    else:
        totals_result = await db.execute(
            text(
                "SELECT COUNT(*) AS total_conversations, "
                "COALESCE(SUM(message_count), 0) AS total_messages "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND is_preview = false"
            ),
            params,
        )
    totals = totals_result.first()
    total_conversations = totals.total_conversations if totals else 0
    total_messages = totals.total_messages if totals else 0
    avg = round(total_messages / total_conversations, 2) if total_conversations else 0.0

    if cutoff is not None:
        top_result = await db.execute(
            text(
                "SELECT first_user_query AS q, COUNT(*) AS c "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND first_user_query IS NOT NULL "
                "AND is_preview = false "
                "AND started_at >= :cutoff "
                "GROUP BY first_user_query "
                "ORDER BY c DESC, q ASC LIMIT 10"
            ),
            params,
        )
    else:
        top_result = await db.execute(
            text(
                "SELECT first_user_query AS q, COUNT(*) AS c "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND first_user_query IS NOT NULL "
                "AND is_preview = false "
                "GROUP BY first_user_query "
                "ORDER BY c DESC, q ASC LIMIT 10"
            ),
            params,
        )
    top_queries = [TopQuery(query=row.q, count=row.c) for row in top_result.all()]

    if cutoff is not None:
        hourly_result = await db.execute(
            text(
                "SELECT EXTRACT(HOUR FROM started_at)::int AS hour, COUNT(*) AS c "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND is_preview = false "
                "AND started_at >= :cutoff "
                "GROUP BY hour"
            ),
            params,
        )
    else:
        hourly_result = await db.execute(
            text(
                "SELECT EXTRACT(HOUR FROM started_at)::int AS hour, COUNT(*) AS c "
                "FROM widget_conversations "
                "WHERE widget_id = CAST(:widget_id AS uuid) "
                "AND is_preview = false "
                "GROUP BY hour"
            ),
            params,
        )
    hourly = [0] * 24
    for row in hourly_result.all():
        if 0 <= row.hour <= 23:
            hourly[row.hour] = row.c

    return WidgetStats(
        period=period,
        total_conversations=total_conversations,
        total_messages=total_messages,
        avg_messages_per_conversation=avg,
        top_queries=top_queries,
        hourly_activity=hourly,
    )
