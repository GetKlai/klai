"""Platform-admin endpoint for per-org widget_messages retention overrides.

SPEC-CHAT-QUALITY-LOOP-001 open item #2: the global default
(``settings.widget_messages_retention_days``, 7 days) applies to every
tenant unless Klai staff set a longer per-org window — e.g. Voys keeps
conversations 90 days so customers can be contacted. Purge behaviour at the
end of the term is unchanged (app/services/widget_messages_retention.py):
messages deleted and contact details cleared; the conversation row itself is kept, judge reasoning nulled, reviews keep
their snapshots.

Endpoints:
    GET   /api/admin/orgs/{slug}/widget-retention — read the current override
    PATCH /api/admin/orgs/{slug}/widget-retention — set or clear it

Both require ``require_platform_admin()``, same as platform-unlocks.
Changes are audited via ``tenant_lifecycle_events`` with
``actor_type='platform_admin'``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import UserPermissions, require_platform_admin
from app.models.portal import PortalOrg
from app.services.audit.tenant_lifecycle import emit_lifecycle_event

logger = structlog.get_logger()

router = APIRouter()


class WidgetRetentionResponse(BaseModel):
    days: int | None
    default_days: int


class PatchWidgetRetentionRequest(BaseModel):
    # Required-but-nullable: only an explicit null resets the override, so a
    # partial-update client cannot shorten a tenant's window by omission.
    days: int | None = Field(..., ge=1, le=365)


async def _get_org_by_slug(slug: str, db: AsyncSession) -> PortalOrg:
    """Load a PortalOrg by slug; raise 404 if not found."""
    result = await db.execute(select(PortalOrg).where(PortalOrg.slug == slug, PortalOrg.deleted_at.is_(None)))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation not found")
    return org


@router.get("/orgs/{slug}/widget-retention", response_model=WidgetRetentionResponse)
async def get_widget_retention(
    slug: str,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> WidgetRetentionResponse:
    """Return the org's widget_messages retention override, or None for the default."""
    org = await _get_org_by_slug(slug, db)
    logger.info(
        "widget_retention_read",
        target_slug=slug,
        days=org.widget_messages_retention_days,
        actor_user_id=perms.user_id,
    )
    return WidgetRetentionResponse(
        days=org.widget_messages_retention_days,
        default_days=settings.widget_messages_retention_days,
    )


@router.patch("/orgs/{slug}/widget-retention", response_model=WidgetRetentionResponse)
async def patch_widget_retention(
    slug: str,
    body: PatchWidgetRetentionRequest,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> WidgetRetentionResponse:
    """Set the org's widget_messages retention override; ``days: null`` resets it to the default."""
    org = await _get_org_by_slug(slug, db)

    previous_days = org.widget_messages_retention_days
    org.widget_messages_retention_days = body.days  # type: ignore[assignment]

    await emit_lifecycle_event(
        db,
        event_type="widget_retention_updated",
        org_id_snapshot=org.id,
        org_slug_snapshot=org.slug,
        org_name_snapshot=org.name,
        actor_user_id=perms.user_id,
        actor_type="platform_admin",
        properties={"previous_days": previous_days, "new_days": body.days},
    )

    await db.commit()

    logger.info(
        "widget_retention_updated",
        target_slug=slug,
        previous_days=previous_days,
        new_days=body.days,
        actor_user_id=perms.user_id,
    )
    return WidgetRetentionResponse(
        days=org.widget_messages_retention_days,
        default_days=settings.widget_messages_retention_days,
    )
