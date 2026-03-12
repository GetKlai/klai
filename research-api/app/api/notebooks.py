"""
Notebook CRUD endpoints:
  POST   /v1/notebooks
  GET    /v1/notebooks
  GET    /v1/notebooks/{nb_id}
  PATCH  /v1/notebooks/{nb_id}
  DELETE /v1/notebooks/{nb_id}
"""
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.models.notebook import Notebook

router = APIRouter(prefix="/v1", tags=["notebooks"])


# ── Request / response models ────────────────────────────────────────────────

class NotebookCreate(BaseModel):
    name: str
    description: str | None = None
    scope: Literal["personal", "org"] = "personal"
    default_mode: Literal["narrow", "broad", "web"] = "narrow"


class NotebookUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_mode: Literal["narrow", "broad", "web"] | None = None
    save_history: bool | None = None


class NotebookResponse(BaseModel):
    id: str
    name: str
    description: str | None
    scope: str
    default_mode: str
    save_history: bool
    sources_count: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class NotebookListResponse(BaseModel):
    items: list[NotebookResponse]
    total: int


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nb_id() -> str:
    return "nb_" + uuid.uuid4().hex[:24]


async def _get_notebook_or_404(
    nb_id: str,
    db: AsyncSession,
    user: CurrentUser,
) -> Notebook:
    result = await db.execute(select(Notebook).where(Notebook.id == nb_id))
    nb: Notebook | None = result.scalar_one_or_none()
    if nb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")

    # Access check
    if nb.scope == "personal" and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")
    if nb.scope == "org" and str(nb.tenant_id) != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")

    return nb


async def _sources_count(db: AsyncSession, nb_id: str) -> int:
    from app.models.source import Source
    result = await db.execute(
        select(func.count()).select_from(Source).where(Source.notebook_id == nb_id)
    )
    return result.scalar_one()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/notebooks", response_model=NotebookResponse, status_code=201)
async def create_notebook(
    body: NotebookCreate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotebookResponse:
    nb = Notebook(
        id=_nb_id(),
        tenant_id=user.tenant_id,
        owner_user_id=user.user_id,
        scope=body.scope,
        name=body.name,
        description=body.description,
        default_mode=body.default_mode,
    )
    db.add(nb)
    await db.commit()
    await db.refresh(nb)

    return NotebookResponse(
        id=nb.id,
        name=nb.name,
        description=nb.description,
        scope=nb.scope,
        default_mode=nb.default_mode,
        save_history=nb.save_history,
        sources_count=0,
        created_at=nb.created_at,
        updated_at=nb.updated_at,
    )


@router.get("/notebooks", response_model=NotebookListResponse)
async def list_notebooks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotebookListResponse:
    # Both branches explicitly include tenant_id to prevent cross-tenant leakage.
    access_filter = and_(
        Notebook.tenant_id == user.tenant_id,
        or_(
            Notebook.owner_user_id == user.user_id,
            Notebook.scope == "org",
        ),
    )

    total_result = await db.execute(
        select(func.count()).select_from(Notebook).where(access_filter)
    )
    total = total_result.scalar_one()

    rows = await db.execute(
        select(Notebook)
        .where(access_filter)
        .order_by(Notebook.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    notebooks = rows.scalars().all()

    items = []
    for nb in notebooks:
        count = await _sources_count(db, nb.id)
        items.append(
            NotebookResponse(
                id=nb.id,
                name=nb.name,
                description=nb.description,
                scope=nb.scope,
                default_mode=nb.default_mode,
                save_history=nb.save_history,
                sources_count=count,
                created_at=nb.created_at,
                updated_at=nb.updated_at,
            )
        )

    return NotebookListResponse(items=items, total=total)


@router.get("/notebooks/{nb_id}", response_model=NotebookResponse)
async def get_notebook(
    nb_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotebookResponse:
    nb = await _get_notebook_or_404(nb_id, db, user)
    count = await _sources_count(db, nb.id)

    return NotebookResponse(
        id=nb.id,
        name=nb.name,
        description=nb.description,
        scope=nb.scope,
        default_mode=nb.default_mode,
        save_history=nb.save_history,
        sources_count=count,
        created_at=nb.created_at,
        updated_at=nb.updated_at,
    )


@router.patch("/notebooks/{nb_id}", response_model=NotebookResponse)
async def update_notebook(
    nb_id: str,
    body: NotebookUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> NotebookResponse:
    nb = await _get_notebook_or_404(nb_id, db, user)

    if nb.scope == "personal" and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang")
    if nb.scope == "org" and not user.is_org_admin():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang")

    if body.name is not None:
        nb.name = body.name
    if body.description is not None:
        nb.description = body.description
    if body.default_mode is not None:
        nb.default_mode = body.default_mode
    if body.save_history is not None:
        nb.save_history = body.save_history
    nb.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(nb)
    count = await _sources_count(db, nb.id)

    return NotebookResponse(
        id=nb.id,
        name=nb.name,
        description=nb.description,
        scope=nb.scope,
        default_mode=nb.default_mode,
        save_history=nb.save_history,
        sources_count=count,
        created_at=nb.created_at,
        updated_at=nb.updated_at,
    )


@router.delete("/notebooks/{nb_id}", status_code=204)
async def delete_notebook(
    nb_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    nb = await _get_notebook_or_404(nb_id, db, user)

    if nb.scope == "personal" and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang")
    if nb.scope == "org" and not user.is_org_admin():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen toegang")

    # Delete chunks first
    from app.models.chunk import Chunk
    from app.models.source import Source

    source_ids_result = await db.execute(
        select(Source.id).where(Source.notebook_id == nb_id)
    )
    source_ids = [row[0] for row in source_ids_result.fetchall()]

    if source_ids:
        await db.execute(delete(Chunk).where(Chunk.source_id.in_(source_ids)))

    await db.execute(delete(Source).where(Source.notebook_id == nb_id))
    await db.execute(delete(Notebook).where(Notebook.id == nb_id))
    await db.commit()
