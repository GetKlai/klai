"""Admin audit log endpoint."""

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit import PortalAuditLog

from . import _get_caller_org, _require_admin, bearer

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AuditLogEntry(BaseModel):
    id: int
    actor_user_id: str
    action: str
    resource_type: str
    resource_id: str
    details: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    items: list[AuditLogEntry]
    total: int
    page: int
    size: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/audit-log", response_model=AuditLogResponse)
async def get_audit_log(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
    page: int = 1,
    size: int = 20,
    action: str | None = None,
    resource_type: str | None = None,
) -> AuditLogResponse:
    """Paginated audit log for the caller's org. Admin only.

    Filters:
    - action: exact match (e.g. "meeting.created")
    - resource_type: exact match (e.g. "group", "meeting", "product", "user")
    """
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    query = select(PortalAuditLog).where(PortalAuditLog.org_id == org.id)
    count_query = select(func.count(PortalAuditLog.id)).where(PortalAuditLog.org_id == org.id)

    if action:
        query = query.where(PortalAuditLog.action == action)
        count_query = count_query.where(PortalAuditLog.action == action)
    if resource_type:
        query = query.where(PortalAuditLog.resource_type == resource_type)
        count_query = count_query.where(PortalAuditLog.resource_type == resource_type)

    total = await db.scalar(count_query) or 0
    offset = (page - 1) * size
    result = await db.execute(query.order_by(PortalAuditLog.created_at.desc()).offset(offset).limit(size))
    items = result.scalars().all()
    return AuditLogResponse(
        items=[AuditLogEntry.model_validate(e) for e in items],
        total=total,
        page=page,
        size=size,
    )
