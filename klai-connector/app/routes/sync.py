"""Sync trigger and history routes."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.enums import SyncStatus
from app.core.logging import get_logger
from app.models.sync_run import SyncRun
from app.routes.deps import get_settings
from app.schemas.sync import SyncRunResponse

logger = get_logger(__name__)

router = APIRouter(tags=["sync"])


def _require_portal_call(request: Request) -> None:
    """Reject requests that did not arrive via the portal internal secret.

    All sync operations are initiated by the portal control plane.
    Direct user calls are no longer supported on these endpoints.
    """
    if not getattr(request.state, "from_portal", False):
        raise HTTPException(status_code=403, detail="Portal service token required")


def _require_portal_org_id(request: Request, settings: Settings) -> str | None:
    """Read ``X-Org-ID`` from the portal-asserted header.

    SPEC-SEC-TENANT-001 REQ-7.3 / REQ-7.6 (v0.5.0):

    - The header value is the Zitadel resourceowner string. The connector
      trusts it because :func:`_require_portal_call` already proved the
      caller holds ``PORTAL_CALLER_SECRET``.
    - During the transition period (``settings.sync_require_org_id=False``):
      a missing header logs ``event="sync_missing_org_id"`` at WARN and
      returns ``None``. Handlers MUST treat ``None`` as
      "skip org scoping" (backward-compat with the pre-REQ-7 portal).
    - Once flipped to True (post portal-side REQ-8.1 deploy + dwell, see
      REQ-8.5 deploy runbook): a missing header raises HTTP 400.

    Empty values are treated identically to absent — no caller should
    ever send ``X-Org-ID:`` with no value, and accepting it would defeat
    the WARN signal.
    """
    raw = request.headers.get("x-org-id")
    org_id = raw.strip() if raw else ""
    if org_id:
        return org_id

    connector_id = request.path_params.get("connector_id")
    logger.warning(
        "sync_missing_org_id",
        extra={
            "event": "sync_missing_org_id",
            "connector_id": str(connector_id) if connector_id else None,
            "path": request.url.path,
        },
    )
    if settings.sync_require_org_id:
        raise HTTPException(status_code=400, detail="X-Org-ID header required")
    return None


@router.post("/connectors/{connector_id}/sync", status_code=202, response_model=SyncRunResponse)
async def trigger_sync(
    connector_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SyncRun:
    """Trigger an on-demand sync for a connector.

    Called by the portal control plane. Returns 202 Accepted with the new
    SyncRun immediately; sync executes in the background.

    Returns 409 if a sync is already in progress for this connector.
    """
    _require_portal_call(request)
    org_id = _require_portal_org_id(request, settings)

    # Active-sync guard. SPEC-SEC-TENANT-001 REQ-7.4: when org_id is
    # asserted, scope the guard so one tenant's running sync cannot block
    # another tenant's trigger attempt for the same connector_id (which
    # cannot legitimately happen — connector_id is per-tenant on the portal
    # side — but the scoping makes the guard's intent explicit and is a
    # belt-and-braces measure for the REQ-7 trust contract).
    active_run_query = select(SyncRun).where(
        SyncRun.connector_id == connector_id,
        SyncRun.status == SyncStatus.RUNNING,
    )
    if org_id is not None:
        active_run_query = active_run_query.where(SyncRun.org_id == org_id)
    active_run_result = await session.execute(active_run_query)
    if active_run_result.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="Sync already running for this connector")

    if org_id is None:
        # SPEC-SEC-TENANT-001 v0.5.1: ``trigger_sync`` rejects missing
        # X-Org-ID even during the transition period. Persisting a row
        # without org_id is technically possible (the column is nullable
        # post-migration 006), but a row that is invisible to every
        # tenant's per-org filter is effectively orphaned at creation
        # time — never queryable, never associated with a triggering
        # caller. Fail-fast at the handler with a deterministic 400
        # rather than create operational debris. The WARN event was
        # already emitted by ``_require_portal_org_id``.
        raise HTTPException(
            status_code=400,
            detail="X-Org-ID header required to create a sync run",
        )

    sync_run = SyncRun(
        connector_id=connector_id,
        org_id=org_id,
        status=SyncStatus.RUNNING,
    )
    session.add(sync_run)
    await session.commit()
    await session.refresh(sync_run)

    sync_engine = getattr(request.app.state, "sync_engine", None)
    if sync_engine:
        background_tasks.add_task(sync_engine.run_sync, connector_id, sync_run.id)

    logger.info(
        "Sync triggered for connector %s",
        connector_id,
        extra={
            "connector_id": str(connector_id),
            "sync_run_id": str(sync_run.id),
            "org_id": org_id,
        },
    )
    return sync_run


@router.get("/connectors/{connector_id}/syncs", response_model=list[SyncRunResponse])
async def list_sync_runs(
    connector_id: uuid.UUID,
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[SyncRun]:
    """List sync history for a connector (most recent first).

    Called by the portal control plane to retrieve sync history for the UI.

    SPEC-SEC-TENANT-001 REQ-7.3: filtered on ``X-Org-ID`` when the header
    is present. During the transition period (REQ-7.6) a missing header
    falls back to legacy connector_id-only filtering with a WARN event.
    """
    _require_portal_call(request)
    org_id = _require_portal_org_id(request, settings)

    query = (
        select(SyncRun)
        .where(SyncRun.connector_id == connector_id)
        .order_by(SyncRun.started_at.desc())
        .limit(min(limit, 100))
    )
    if org_id is not None:
        query = query.where(SyncRun.org_id == org_id)

    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/connectors/{connector_id}/syncs/{run_id}", response_model=SyncRunResponse)
async def get_sync_run(
    connector_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SyncRun:
    """Get details of a specific sync run.

    SPEC-SEC-TENANT-001 REQ-7.3 / REQ-7.5: filters on org_id when
    asserted. A run that exists but belongs to a different tenant
    returns 404 — never 403 — to avoid leaking existence
    (``portal-security.md`` "never leak existence").
    """
    _require_portal_call(request)
    org_id = _require_portal_org_id(request, settings)

    sync_run = await session.get(SyncRun, run_id)
    if sync_run is None or sync_run.connector_id != connector_id:
        raise HTTPException(status_code=404, detail="Sync run not found")

    # REQ-7.5: cross-tenant run -> 404, not 403.
    if org_id is not None and sync_run.org_id != org_id:
        raise HTTPException(status_code=404, detail="Sync run not found")

    return sync_run
