"""Connector CRUD routes (POST/GET/PUT/DELETE) with org_id scoping."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.logging import get_logger
from app.models.connector import Connector
from app.routes.deps import get_org_id
from app.schemas.connector import ConnectorCreate, ConnectorResponse, ConnectorUpdate

logger = get_logger(__name__)

router = APIRouter(prefix="/connectors", tags=["connectors"])


@router.post("", status_code=201, response_model=ConnectorResponse)
async def create_connector(
    body: ConnectorCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Connector:
    """Create a new connector configuration.

    The connector is scoped to the authenticated user's org_id.
    """
    org_id = get_org_id(request)
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


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[Connector]:
    """List all connectors belonging to the authenticated org."""
    org_id = get_org_id(request)
    result = await session.execute(
        select(Connector).where(Connector.org_id == org_id).order_by(Connector.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector(
    connector_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Connector:
    """Get a single connector by ID, scoped to org."""
    org_id = get_org_id(request)
    connector = await session.get(Connector, connector_id)
    if connector is None or connector.org_id != org_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    return connector


@router.put("/{connector_id}", response_model=ConnectorResponse)
async def update_connector(
    connector_id: uuid.UUID,
    body: ConnectorUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Connector:
    """Update a connector configuration."""
    org_id = get_org_id(request)
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


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a connector and all associated sync runs."""
    org_id = get_org_id(request)
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
