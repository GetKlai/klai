"""Connector CRUD routes (POST/GET/PUT/DELETE) with org_id scoping."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session, set_tenant
from app.core.logging import get_logger
from app.models.connector import Connector
from app.routes.deps import enforce_org_rate_limit, get_org_id
from app.routes.sync import _require_portal_call  # pyright: ignore[reportPrivateUsage]
from app.schemas.connector import ConnectorCreate, ConnectorResponse, ConnectorUpdate

logger = get_logger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post(
    "",
    status_code=201,
    response_model=ConnectorResponse,
    dependencies=[Depends(enforce_org_rate_limit("write"))],
)
async def create_connector(
    body: ConnectorCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Connector:
    """Create a new connector configuration.

    The connector is scoped to the authenticated user's org_id.
    """
    org_id = get_org_id(request)
    await set_tenant(session, org_id)  # SPEC-TI-002: Cat-D RLS context
    connector = Connector(
        org_id=org_id,
        name=body.name,
        connector_type=body.connector_type,
        config=body.config,
        schedule=body.schedule,
    )
    session.add(connector)
    await session.commit()
    await session.refresh(connector)
    logger.info("Connector created: %s", connector.id, extra={"org_id": str(org_id)})

    # Update scheduler if schedule is set
    app = request.app
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler and connector.schedule:
        scheduler.add_job(connector)

    return connector


@router.get(
    "",
    response_model=list[ConnectorResponse],
    dependencies=[Depends(enforce_org_rate_limit("read"))],
)
async def list_connectors(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[Connector]:
    """List all connectors belonging to the authenticated org."""
    org_id = get_org_id(request)
    await set_tenant(session, org_id)  # SPEC-TI-002: Cat-D RLS context
    result = await session.execute(
        select(Connector).where(Connector.org_id == org_id).order_by(Connector.created_at.desc())
    )
    return list(result.scalars().all())


@router.get(
    "/{connector_id}",
    response_model=ConnectorResponse,
    dependencies=[Depends(enforce_org_rate_limit("read"))],
)
async def get_connector(
    connector_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Connector:
    """Get a single connector by ID, scoped to org."""
    org_id = get_org_id(request)
    await set_tenant(session, org_id)  # SPEC-TI-002: Cat-D RLS context
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


@router.put(
    "/{connector_id}",
    response_model=ConnectorResponse,
    dependencies=[Depends(enforce_org_rate_limit("write"))],
)
async def update_connector(
    connector_id: uuid.UUID,
    body: ConnectorUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Connector:
    """Update a connector configuration."""
    org_id = get_org_id(request)
    await set_tenant(session, org_id)  # SPEC-TI-002: Cat-D RLS context
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(connector, field, value)

    await session.commit()
    await session.refresh(connector)
    logger.info("Connector updated: %s", connector.id, extra={"org_id": str(org_id)})

    # Update scheduler
    app = request.app
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.remove_job(connector.id)
        if connector.schedule and connector.is_enabled:
            scheduler.add_job(connector)

    return connector


@router.delete(
    "/{connector_id}",
    status_code=204,
    dependencies=[Depends(enforce_org_rate_limit("write"))],
)
async def delete_connector(
    connector_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a connector and all associated sync runs."""
    org_id = get_org_id(request)
    await set_tenant(session, org_id)  # SPEC-TI-002: Cat-D RLS context
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")

    # Remove scheduled job
    app = request.app
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        scheduler.remove_job(connector.id)

    await session.delete(connector)
    await session.commit()
    logger.info("Connector deleted: %s", connector_id, extra={"org_id": str(org_id)})


@router.get("/{connector_id}/ms-docs/folders")
async def list_ms_docs_folders(
    connector_id: uuid.UUID,
    request: Request,
    parent: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    """List child folders of a Microsoft 365 connector's drive.

    Powers the post-OAuth folder picker in the portal. Called exclusively
    by the portal-api control plane, NOT by an end-user — uses the
    ``_require_portal_call`` bypass (X-Internal-Secret) for auth and
    fetches the live connector config + credentials from portal via
    ``PortalClient`` so this stateless service does not need a local
    copy of the OAuth tokens.

    Errors:
        400 — not an ms_docs connector.
        404 — connector not found in portal (deleted between portal
              fetch and the picker open).
        502 — adapter raised a Graph error.
        503 — adapter not registered (MS_DOCS_CLIENT_ID unset).
    """
    _require_portal_call(request)

    portal_client = getattr(request.app.state, "portal_client", None)
    if portal_client is None:
        raise HTTPException(status_code=503, detail="Portal client unavailable")
    try:
        portal_config = await portal_client.get_connector_config(connector_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Connector not found") from exc
        logger.exception("Portal config fetch failed for %s", connector_id)
        raise HTTPException(status_code=502, detail="Portal config fetch failed") from exc

    if portal_config.connector_type != "ms_docs":
        raise HTTPException(status_code=400, detail="Not an ms_docs connector")

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Adapter registry unavailable")
    try:
        adapter = registry.get("ms_docs")
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="ms_docs adapter not registered on this deployment",
        ) from None

    parent_id = parent.strip() if parent else None
    try:
        folders = await adapter.list_folders(portal_config, parent_id=parent_id)
    except httpx.HTTPStatusError as exc:
        logger.exception("ms_docs list_folders failed for %s", connector_id)
        raise HTTPException(status_code=502, detail=f"Microsoft Graph error: {exc.response.status_code}") from exc
    return {"folders": folders}


@router.get("/{connector_id}/google-drive/folders")
async def list_google_drive_folders(
    connector_id: uuid.UUID,
    request: Request,
    parent: str | None = None,
) -> dict[str, list[dict[str, object]]]:
    """List child Drive items for a Google Drive / Workspace connector.

    Powers the post-OAuth picker in the portal. The endpoint accepts the
    ``google_docs`` / ``google_sheets`` / ``google_slides`` aliases; the
    adapter applies their content-type presets when listing files.
    """
    _require_portal_call(request)

    portal_client = getattr(request.app.state, "portal_client", None)
    if portal_client is None:
        raise HTTPException(status_code=503, detail="Portal client unavailable")
    try:
        portal_config = await portal_client.get_connector_config(connector_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Connector not found") from exc
        logger.exception("Portal config fetch failed for %s", connector_id)
        raise HTTPException(status_code=502, detail="Portal config fetch failed") from exc

    if portal_config.connector_type not in {
        "google_drive",
        "google_docs",
        "google_sheets",
        "google_slides",
    }:
        raise HTTPException(status_code=400, detail="Not a Google Drive connector")

    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Adapter registry unavailable")
    try:
        adapter = registry.get(portal_config.connector_type)
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="google_drive adapter not registered on this deployment",
        ) from None

    parent_id = parent.strip() if parent else None
    try:
        folders = await adapter.list_folders(portal_config, parent_id=parent_id)  # type: ignore[attr-defined]
    except httpx.HTTPStatusError as exc:
        logger.exception("google_drive list_folders failed for %s", connector_id)
        raise HTTPException(status_code=502, detail=f"Google Drive error: {exc.response.status_code}") from exc
    return {"folders": folders}
