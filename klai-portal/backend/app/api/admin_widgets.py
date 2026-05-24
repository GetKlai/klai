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
from app.services.widget_auth import generate_session_token

logger = structlog.get_logger()

router = APIRouter(prefix="/api/admin/widgets", tags=["Widgets Admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


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
    widget_position: str = "right"  # 'left' | 'right'


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
            widget_position=config.get("widget_position", "right"),
        ),
        public_share_enabled=widget.public_share_enabled,
        allow_any_origin=getattr(widget, "allow_any_origin", False),
        kb_access_count=kb_access_count,
        rate_limit_rpm=widget.rate_limit_rpm,
        last_used_at=str(widget.last_used_at) if widget.last_used_at else None,
        created_at=str(widget.created_at),
        created_by=widget.created_by,
    )


async def _get_widget_or_404(widget_id: str, org_id: int, db: AsyncSession) -> Widget:
    result = await db.execute(
        select(Widget).where(
            Widget.id == widget_id,
            Widget.org_id == org_id,
        )
    )
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
    result = await db.execute(select(Widget).where(Widget.org_id == perms.org_id))
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

    token = generate_session_token(
        wgt_id=widget.widget_id,
        org_id=widget.org_id,
        kb_ids=kb_ids,
        secret=settings.widget_jwt_secret,
        tenant_slug=org.slug,
    )
    return PreviewSessionResponse(
        session_token=token,
        chat_endpoint="/partner/v1/chat/completions",
        session_expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


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
    """Permanently delete a widget and its KB access entries."""
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)

    await db.execute(delete(WidgetKbAccess).where(WidgetKbAccess.widget_id == widget.id))
    await db.execute(
        delete(Widget).where(
            Widget.id == widget.id,
            Widget.org_id == perms.org_id,
        )
    )
    await db.commit()

    emit_event(
        "widget.deleted",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"widget_id": widget.id, "name": widget.name},
    )
    logger.info("Widget deleted", widget_id=widget.id, org_id=perms.org_id)


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
    db: AsyncSession = Depends(get_db),
) -> list[ConversationListItem]:
    """Paginated list of conversations for one widget, newest first.

    Cursor = ISO timestamp of the last row returned. Pass it to
    ``cursor`` to fetch the next page (``started_at < cursor``).
    """
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)

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
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    """Full transcript of one conversation, messages in chronological order."""
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)

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
    db: AsyncSession = Depends(get_db),
) -> WidgetStats:
    """Aggregate metrics for the Activiteit tab.

    Three queries: totals, top 10 first-user-queries, and 24 hourly
    buckets. The ``period`` filter scopes everything to a rolling
    window of 7 / 30 days, or all-time."""
    widget = await _get_widget_or_404(widget_id, perms.org_id, db)
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
                "WHERE widget_id = CAST(:widget_id AS uuid)"
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
