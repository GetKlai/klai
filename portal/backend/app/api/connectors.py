"""App-facing API for Knowledge Base Connectors."""

import logging
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services.access import get_user_role_for_kb
from app.services.klai_connector_client import SyncRunData, klai_connector_client

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/app/knowledge-bases/{kb_slug}/connectors",
    tags=["connectors"],
)

ConnectorType = Literal["github", "notion", "web_crawler", "google_drive", "ms_docs"]


# -- Pydantic schemas --------------------------------------------------------


class ConnectorCreateRequest(BaseModel):
    name: str
    connector_type: ConnectorType
    config: dict = Field(default_factory=dict)
    schedule: str | None = None


class ConnectorUpdateRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    schedule: str | None = None
    is_enabled: bool | None = None


class ConnectorOut(BaseModel):
    id: str
    kb_id: int
    name: str
    connector_type: str
    config: dict
    schedule: str | None
    is_enabled: bool
    last_sync_at: datetime | None
    last_sync_status: str | None
    created_at: datetime
    created_by: str


# -- Helpers ------------------------------------------------------------------


async def _get_kb_with_owner_check(
    kb_slug: str,
    caller_id: str,
    org_id: int,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Look up KB by slug + org_id and verify caller has owner role."""
    kb = await _get_kb_for_org(kb_slug, org_id, db)
    role = await get_user_role_for_kb(kb.id, caller_id, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required to manage connectors",
        )
    return kb


async def _get_kb_for_org(
    kb_slug: str,
    org_id: int,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Look up KB by slug + org_id (read-only, no role check)."""
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.slug == kb_slug,
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    return kb


def _connector_out(c: PortalConnector) -> ConnectorOut:
    return ConnectorOut(
        id=str(c.id),
        kb_id=c.kb_id,
        name=c.name,
        connector_type=c.connector_type,
        config=c.config,
        schedule=c.schedule,
        is_enabled=c.is_enabled,
        last_sync_at=c.last_sync_at,
        last_sync_status=c.last_sync_status,
        created_at=c.created_at,
        created_by=c.created_by,
    )


# -- Endpoints ----------------------------------------------------------------


@router.get("/", response_model=list[ConnectorOut])
async def list_connectors(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectorOut]:
    """List connectors for a KB. Any org member with access to the KB can view."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_for_org(kb_slug, org.id, db)
    result = await db.execute(select(PortalConnector).where(PortalConnector.kb_id == kb.id))
    return [_connector_out(c) for c in result.scalars().all()]


@router.post("/", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def create_connector(
    kb_slug: str,
    body: ConnectorCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ConnectorOut:
    """Create a connector for a KB. Requires contributor access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    connector = PortalConnector(
        kb_id=kb.id,
        org_id=org.id,
        name=body.name,
        connector_type=body.connector_type,
        config=body.config,
        schedule=body.schedule,
        created_by=caller_id,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return _connector_out(connector)


@router.patch("/{connector_id}", response_model=ConnectorOut)
async def update_connector(
    kb_slug: str,
    connector_id: str,
    body: ConnectorUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ConnectorOut:
    """Update a connector. Requires contributor access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    if body.name is not None:
        connector.name = body.name
    if body.config is not None:
        connector.config = body.config
    if body.schedule is not None:
        connector.schedule = body.schedule
    if body.is_enabled is not None:
        connector.is_enabled = body.is_enabled
    await db.commit()
    await db.refresh(connector)
    return _connector_out(connector)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    kb_slug: str,
    connector_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a connector. Requires contributor access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    await db.delete(connector)
    await db.commit()


@router.post("/{connector_id}/sync", response_model=SyncRunData, status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    kb_slug: str,
    connector_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> SyncRunData:
    """Trigger an on-demand sync for a connector. Requires owner access.

    Delegates to klai-connector execution service. Returns 202 with the new
    SyncRun immediately; sync runs in the background.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    if not connector.is_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connector is disabled")
    if connector.last_sync_status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync already running")

    try:
        sync_run = await klai_connector_client.trigger_sync(connector_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == status.HTTP_409_CONFLICT:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync already running") from exc
        logger.exception("klai-connector returned error for connector %s", connector_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Sync service error") from exc
    except httpx.HTTPError as exc:
        logger.exception("Failed to reach klai-connector for connector %s", connector_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Sync service unavailable") from exc

    # Optimistically mark as running so the UI reflects it immediately.
    connector.last_sync_status = "running"
    await db.commit()
    return sync_run


@router.get("/{connector_id}/syncs", response_model=list[SyncRunData])
async def list_sync_runs(
    kb_slug: str,
    connector_id: str,
    limit: int = 20,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[SyncRunData]:
    """List sync history for a connector (most recent first).

    Proxies to klai-connector execution service.
    """
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_for_org(kb_slug, org.id, db)
    exists = await db.execute(
        select(PortalConnector.id).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    if not exists.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")

    try:
        return await klai_connector_client.get_sync_runs(connector_id, limit=limit)
    except httpx.HTTPError as exc:
        logger.exception("Failed to reach klai-connector for sync history of %s", connector_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Sync service unavailable") from exc
