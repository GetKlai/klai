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

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least, require_platform_unlocked
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.widgets import Widget, WidgetKbAccess, generate_widget_id
from app.services.events import emit_event

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


class CreateWidgetRequest(BaseModel):
    name: str = Field(min_length=3, max_length=128)
    description: str | None = None
    kb_ids: list[int] = Field(default_factory=list)
    rate_limit_rpm: int = Field(default=60, ge=10, le=600)
    widget_config: WidgetConfig | None = None


class WidgetResponse(BaseModel):
    id: str
    name: str
    description: str | None
    widget_id: str
    widget_config: WidgetConfig
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
        ),
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

    widget_row = Widget(
        id=internal_id,
        org_id=perms.org_id,
        name=body.name,
        description=body.description,
        widget_id=widget_id_str,
        widget_config=config,
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
