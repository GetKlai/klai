"""Sync trigger and history routes."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.connector import Connector
from app.models.sync_run import SyncRun
from app.routes.deps import get_org_id
from app.schemas.sync import SyncRunResponse

logger = get_logger(__name__)

router = APIRouter(tags=["sync"])


@router.post("/connectors/{connector_id}/sync", status_code=202, response_model=SyncRunResponse)
async def trigger_sync(
    connector_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> SyncRun:
    """Trigger an on-demand sync for a connector.

    Returns 202 Accepted with the new SyncRun immediately.
    Returns 409 if a sync is already running for this connector.
    """
    org_id = get_org_id(request)
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")

    if not connector.is_enabled:
        raise HTTPException(status_code=400, detail="Connector is disabled")

    # Check for active sync
    active_run_result = await session.execute(
        select(SyncRun).where(
            SyncRun.connector_id == connector_id,
            SyncRun.status == SyncStatus.RUNNING,
        )
    )
    if active_run_result.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="Sync already running for this connector")

    # Create sync run record
    sync_run = SyncRun(
        connector_id=connector_id,
        status=SyncStatus.RUNNING,
    )
    session.add(sync_run)
    await session.commit()
    await session.refresh(sync_run)

    # Schedule background sync
    sync_engine = getattr(request.app.state, "sync_engine", None)
    if sync_engine:
        background_tasks.add_task(sync_engine.run_sync, connector_id, sync_run.id)

    logger.info(
        "Sync triggered for connector %s", connector_id,
        extra={"connector_id": str(connector_id), "sync_run_id": str(sync_run.id)},
    )
    return sync_run


@router.get("/connectors/{connector_id}/syncs", response_model=list[SyncRunResponse])
async def list_sync_runs(
    connector_id: uuid.UUID,
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> list[SyncRun]:
    """List sync history for a connector (most recent first)."""
    org_id = get_org_id(request)
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")

    result = await session.execute(
        select(SyncRun)
        .where(SyncRun.connector_id == connector_id)
        .order_by(SyncRun.started_at.desc())
        .limit(min(limit, 100))
    )
    return list(result.scalars().all())


@router.get("/connectors/{connector_id}/syncs/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(
    connector_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SyncRun:
    """Get details of a specific sync run."""
    org_id = get_org_id(request)
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")

    sync_run = await session.get(SyncRun, run_id)
    if sync_run is None or sync_run.connector_id != connector_id:
        raise HTTPException(status_code=404, detail="Sync run not found")

    return sync_run
