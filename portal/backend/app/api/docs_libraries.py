"""Admin API for managing Docs Libraries and their group access."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, _require_admin_or_group_admin_role, bearer
from app.core.database import get_db
from app.models.docs import PortalDocsLibrary, PortalGroupDocsAccess
from app.models.groups import PortalGroup

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["docs-libraries"])


# -- Pydantic schemas --------------------------------------------------------


class DocsCreateRequest(BaseModel):
    name: str
    slug: str
    description: str | None = None


class DocsUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class DocsOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    created_at: datetime
    created_by: str


class DocsListResponse(BaseModel):
    docs_libraries: list[DocsOut]


class DocsGroupAccessOut(BaseModel):
    group_id: int
    group_name: str
    granted_at: datetime
    granted_by: str


class DocsGroupsResponse(BaseModel):
    groups: list[DocsGroupAccessOut]


class DocsGroupGrantRequest(BaseModel):
    group_id: int


class MessageResponse(BaseModel):
    message: str


# -- Docs Library CRUD -------------------------------------------------------


@router.get("/docs-libraries", response_model=DocsListResponse)
async def list_docs_libraries(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> DocsListResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    result = await db.execute(
        select(PortalDocsLibrary)
        .where(PortalDocsLibrary.org_id == org.id)
        .order_by(PortalDocsLibrary.name)
    )
    libs = result.scalars().all()
    return DocsListResponse(
        docs_libraries=[
            DocsOut(
                id=lib.id,
                name=lib.name,
                slug=lib.slug,
                description=lib.description,
                created_at=lib.created_at,
                created_by=lib.created_by,
            )
            for lib in libs
        ]
    )


@router.post("/docs-libraries", response_model=DocsOut, status_code=status.HTTP_201_CREATED)
async def create_docs_library(
    body: DocsCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> DocsOut:
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    lib = PortalDocsLibrary(
        org_id=org.id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        created_by=caller_id,
    )
    db.add(lib)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Slug bestaat al in deze organisatie",
        ) from exc
    await db.commit()
    await db.refresh(lib)
    return DocsOut(
        id=lib.id,
        name=lib.name,
        slug=lib.slug,
        description=lib.description,
        created_at=lib.created_at,
        created_by=lib.created_by,
    )


@router.patch("/docs-libraries/{library_id}", response_model=DocsOut)
async def update_docs_library(
    library_id: int,
    body: DocsUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> DocsOut:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    result = await db.execute(
        select(PortalDocsLibrary).where(
            PortalDocsLibrary.id == library_id,
            PortalDocsLibrary.org_id == org.id,
        )
    )
    lib = result.scalar_one_or_none()
    if not lib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Docs library niet gevonden")
    if body.name is not None:
        lib.name = body.name
    if body.description is not None:
        lib.description = body.description
    await db.commit()
    await db.refresh(lib)
    return DocsOut(
        id=lib.id,
        name=lib.name,
        slug=lib.slug,
        description=lib.description,
        created_at=lib.created_at,
        created_by=lib.created_by,
    )


@router.delete("/docs-libraries/{library_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_docs_library(
    library_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    result = await db.execute(
        select(PortalDocsLibrary).where(
            PortalDocsLibrary.id == library_id,
            PortalDocsLibrary.org_id == org.id,
        )
    )
    lib = result.scalar_one_or_none()
    if not lib:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Docs library niet gevonden")
    await db.delete(lib)
    await db.commit()


# -- Group access for Docs Libraries -----------------------------------------


@router.get("/docs-libraries/{library_id}/groups", response_model=DocsGroupsResponse)
async def list_docs_library_groups(
    library_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> DocsGroupsResponse:
    _, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    result = await db.execute(
        select(PortalDocsLibrary).where(
            PortalDocsLibrary.id == library_id,
            PortalDocsLibrary.org_id == org.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Docs library niet gevonden")
    access_result = await db.execute(
        select(PortalGroupDocsAccess, PortalGroup)
        .join(PortalGroup, PortalGroup.id == PortalGroupDocsAccess.group_id)
        .where(PortalGroupDocsAccess.library_id == library_id)
        .order_by(PortalGroup.name)
    )
    rows = access_result.all()
    return DocsGroupsResponse(
        groups=[
            DocsGroupAccessOut(
                group_id=a.group_id,
                group_name=g.name,
                granted_at=a.granted_at,
                granted_by=a.granted_by,
            )
            for a, g in rows
        ]
    )


@router.post(
    "/docs-libraries/{library_id}/groups",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_docs_library_group_access(
    library_id: int,
    body: DocsGroupGrantRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    caller_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    lib_result = await db.execute(
        select(PortalDocsLibrary).where(
            PortalDocsLibrary.id == library_id,
            PortalDocsLibrary.org_id == org.id,
        )
    )
    if not lib_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Docs library niet gevonden")
    group_result = await db.execute(
        select(PortalGroup).where(
            PortalGroup.id == body.group_id,
            PortalGroup.org_id == org.id,
        )
    )
    if not group_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Groep niet gevonden")
    access = PortalGroupDocsAccess(group_id=body.group_id, library_id=library_id, granted_by=caller_id)
    db.add(access)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Groep heeft al toegang tot deze docs library",
        ) from exc
    await db.commit()
    return MessageResponse(message="Toegang verleend")


@router.delete("/docs-libraries/{library_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_docs_library_group_access(
    library_id: int,
    group_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    _, _org, caller_user = await _get_caller_org(credentials, db)
    _require_admin_or_group_admin_role(caller_user)
    result = await db.execute(
        select(PortalGroupDocsAccess).where(
            PortalGroupDocsAccess.library_id == library_id,
            PortalGroupDocsAccess.group_id == group_id,
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Toegang niet gevonden")
    await db.delete(access)
    await db.commit()
