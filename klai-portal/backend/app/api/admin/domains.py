"""
Admin endpoints for managing allowed email domains (SPEC-AUTH-006 R3).

GET    /api/admin/domains         -- list org's allowed domains
POST   /api/admin/domains         -- add a new allowed domain
DELETE /api/admin/domains/{id}    -- remove an allowed domain
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import _get_caller_org, _require_admin, bearer
from app.core.database import get_db
from app.models.portal import PortalOrgAllowedDomain
from app.services.domain_validation import is_free_email_provider, is_valid_domain, normalize_domain

logger = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class DomainItem(BaseModel):
    id: int
    domain: str
    created_at: str
    created_by: str


class DomainsResponse(BaseModel):
    domains: list[DomainItem]


class AddDomainRequest(BaseModel):
    domain: str


class AddDomainResponse(BaseModel):
    id: int
    domain: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/domains", response_model=DomainsResponse)
async def list_domains(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> DomainsResponse:
    """List all allowed email domains for the caller's org."""
    _zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalOrgAllowedDomain)
        .where(PortalOrgAllowedDomain.org_id == org.id)
        .order_by(PortalOrgAllowedDomain.created_at.desc())
    )
    rows = result.scalars().all()

    return DomainsResponse(
        domains=[
            DomainItem(
                id=row.id,
                domain=row.domain,
                created_at=str(row.created_at),
                created_by=row.created_by,
            )
            for row in rows
        ]
    )


@router.post("/domains", response_model=AddDomainResponse, status_code=status.HTTP_201_CREATED)
async def add_domain(
    body: AddDomainRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> AddDomainResponse:
    """Add a new allowed email domain for the caller's org."""
    zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    domain = normalize_domain(body.domain)

    if not is_valid_domain(domain):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid domain format",
        )

    if is_free_email_provider(domain):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Free email providers cannot be used as allowed domains",
        )

    new_domain = PortalOrgAllowedDomain(
        org_id=org.id,
        domain=domain,
        created_by=zitadel_user_id,
    )
    db.add(new_domain)

    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Domain already exists",
        ) from exc

    await db.commit()
    await db.refresh(new_domain)

    logger.info("Domain added", org_id=org.id, domain=domain, added_by=zitadel_user_id)
    return AddDomainResponse(id=new_domain.id, domain=new_domain.domain)


@router.delete("/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_domain(
    domain_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove an allowed email domain (org-scoped)."""
    zitadel_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(
        select(PortalOrgAllowedDomain).where(
            PortalOrgAllowedDomain.id == domain_id,
            PortalOrgAllowedDomain.org_id == org.id,
        )
    )
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain not found",
        )

    await db.delete(domain)
    await db.commit()

    logger.info("Domain removed", org_id=org.id, domain=domain.domain, removed_by=zitadel_user_id)
