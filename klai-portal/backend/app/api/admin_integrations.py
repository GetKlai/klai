"""Admin integration management endpoints (SPEC-API-001 REQ-6).

CRUD for partner API keys ("integrations") scoped to caller's org.
Auth: Zitadel OIDC session with admin/owner role check.
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import _get_caller_org, _require_admin
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.services.events import emit_event
from app.services.partner_keys import generate_partner_key

logger = structlog.get_logger()
bearer = HTTPBearer()

router = APIRouter(prefix="/api/integrations", tags=["Integrations Admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class KbAccessEntry(BaseModel):
    kb_id: int
    access_level: Literal["read", "read_write"]


class CreateIntegrationRequest(BaseModel):
    name: str = Field(min_length=3, max_length=128)
    description: str | None = None
    permissions: dict  # {"chat": bool, "feedback": bool, "knowledge_append": bool}
    kb_access: list[KbAccessEntry]
    rate_limit_rpm: int = Field(default=60, ge=10, le=600)


class IntegrationResponse(BaseModel):
    id: str
    name: str
    description: str | None
    key_prefix: str
    permissions: dict
    active: bool
    kb_access_count: int
    rate_limit_rpm: int
    last_used_at: str | None
    created_at: str
    created_by: str


class CreateIntegrationResponse(IntegrationResponse):
    api_key: str  # Full plaintext key — only in create response


class IntegrationDetailResponse(IntegrationResponse):
    kb_access: list[dict]  # [{kb_id, kb_name, kb_slug, access_level}]


class UpdateIntegrationRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: dict | None = None
    kb_access: list[KbAccessEntry] | None = None
    rate_limit_rpm: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key_to_response(key: PartnerAPIKey, kb_access_count: int) -> IntegrationResponse:
    return IntegrationResponse(
        id=key.id,
        name=key.name,
        description=key.description,
        key_prefix=key.key_prefix,
        permissions=key.permissions,
        active=key.active,
        kb_access_count=kb_access_count,
        rate_limit_rpm=key.rate_limit_rpm,
        last_used_at=str(key.last_used_at) if key.last_used_at else None,
        created_at=str(key.created_at),
        created_by=key.created_by,
    )


async def _get_integration_or_404(integration_id: str, org_id: int, db: AsyncSession) -> PartnerAPIKey:
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.id == integration_id,
            PartnerAPIKey.org_id == org_id,
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Integration not found")
    return key


async def _validate_kb_ids(kb_ids: list[int], org_id: int, db: AsyncSession) -> list[PortalKnowledgeBase]:
    """Validate that all kb_ids belong to the caller's org."""
    if not kb_ids:
        return []
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id.in_(kb_ids),
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    found_kbs = result.scalars().all()
    found_ids = {kb.id for kb in found_kbs}
    missing = set(kb_ids) - found_ids
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge base IDs not found in your organisation: {sorted(missing)}",
        )
    return list(found_kbs)


async def _count_kb_access(key_id: str, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(PartnerApiKeyKbAccess)
        .where(PartnerApiKeyKbAccess.partner_api_key_id == key_id)
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# POST /api/integrations
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_integration(
    body: CreateIntegrationRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CreateIntegrationResponse:
    """Create a new partner API key integration. REQ-6.2."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    # Validate KB IDs belong to org
    kb_ids = [entry.kb_id for entry in body.kb_access]
    await _validate_kb_ids(kb_ids, org.id, db)

    # Validate: knowledge_append requires at least one read_write KB
    if body.permissions.get("knowledge_append"):
        if not any(e.access_level == "read_write" for e in body.kb_access):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="knowledge_append permission requires at least one KB with read_write access",
            )

    # Generate key
    plaintext_key, key_hash = generate_partner_key()
    key_id = str(uuid.uuid4())

    # Create key + KB access rows via ORM
    key_row = PartnerAPIKey(
        id=key_id,
        org_id=org.id,
        name=body.name,
        description=body.description,
        key_prefix=plaintext_key[:12],
        key_hash=key_hash,
        permissions=body.permissions,
        rate_limit_rpm=body.rate_limit_rpm,
        created_by=caller_user_id,
    )
    db.add(key_row)

    for entry in body.kb_access:
        db.add(
            PartnerApiKeyKbAccess(
                partner_api_key_id=key_id,
                kb_id=entry.kb_id,
                access_level=entry.access_level,
            )
        )

    await db.commit()
    await db.refresh(key_row)  # load server-generated created_at

    emit_event(
        "integration.created",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key_id, "name": body.name},
    )
    logger.info("Integration created", integration_id=key_id, org_id=org.id)

    return CreateIntegrationResponse(
        id=key_row.id,
        name=key_row.name,
        description=key_row.description,
        key_prefix=key_row.key_prefix,
        permissions=key_row.permissions,
        active=True,
        kb_access_count=len(body.kb_access),
        rate_limit_rpm=key_row.rate_limit_rpm,
        last_used_at=None,
        created_at=str(key_row.created_at),
        created_by=key_row.created_by,
        api_key=plaintext_key,
    )


# ---------------------------------------------------------------------------
# GET /api/integrations
# ---------------------------------------------------------------------------


@router.get("")
async def list_integrations(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[IntegrationResponse]:
    """List all integrations for the caller's org. REQ-6.3."""
    _caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(select(PartnerAPIKey).where(PartnerAPIKey.org_id == org.id))
    keys = result.scalars().all()
    if not keys:
        return []

    # Count KB access per key in one query
    key_ids = [k.id for k in keys]
    count_result = await db.execute(
        select(
            PartnerApiKeyKbAccess.partner_api_key_id,
            func.count().label("cnt"),
        )
        .where(PartnerApiKeyKbAccess.partner_api_key_id.in_(key_ids))
        .group_by(PartnerApiKeyKbAccess.partner_api_key_id)
    )
    kb_counts = {row.partner_api_key_id: row.cnt for row in count_result}

    return [_key_to_response(k, kb_counts.get(k.id, 0)) for k in keys]


# ---------------------------------------------------------------------------
# GET /api/integrations/{id}
# ---------------------------------------------------------------------------


@router.get("/{integration_id}")
async def get_integration_detail(
    integration_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> IntegrationDetailResponse:
    """Get full detail for a single integration. REQ-6.4."""
    _caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_integration_or_404(integration_id, org.id, db)

    # Load KB access with KB names in one query
    kb_result = await db.execute(
        select(PartnerApiKeyKbAccess, PortalKnowledgeBase)
        .join(PortalKnowledgeBase, PartnerApiKeyKbAccess.kb_id == PortalKnowledgeBase.id)
        .where(PartnerApiKeyKbAccess.partner_api_key_id == key.id)
    )
    kb_access_list = [
        {
            "kb_id": access.kb_id,
            "kb_name": kb.name,
            "kb_slug": kb.slug,
            "access_level": access.access_level,
        }
        for access, kb in kb_result
    ]

    return IntegrationDetailResponse(
        id=key.id,
        name=key.name,
        description=key.description,
        key_prefix=key.key_prefix,
        permissions=key.permissions,
        active=key.active,
        kb_access_count=len(kb_access_list),
        rate_limit_rpm=key.rate_limit_rpm,
        last_used_at=str(key.last_used_at) if key.last_used_at else None,
        created_at=str(key.created_at),
        created_by=key.created_by,
        kb_access=kb_access_list,
    )


# ---------------------------------------------------------------------------
# PATCH /api/integrations/{id}
# ---------------------------------------------------------------------------


@router.patch("/{integration_id}")
async def update_integration(
    integration_id: str,
    body: UpdateIntegrationRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> IntegrationResponse:
    """Partial update of an integration. REQ-6.5."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_integration_or_404(integration_id, org.id, db)

    if not key.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot update a revoked integration")

    # Apply partial field updates
    if body.name is not None:
        key.name = body.name
    if body.description is not None:
        key.description = body.description
    if body.permissions is not None:
        key.permissions = body.permissions
    if body.rate_limit_rpm is not None:
        key.rate_limit_rpm = body.rate_limit_rpm

    # Atomic KB access replacement
    if body.kb_access is not None:
        kb_ids = [entry.kb_id for entry in body.kb_access]
        await _validate_kb_ids(kb_ids, org.id, db)

        await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))
        for entry in body.kb_access:
            db.add(
                PartnerApiKeyKbAccess(
                    partner_api_key_id=key.id,
                    kb_id=entry.kb_id,
                    access_level=entry.access_level,
                )
            )

    await db.commit()

    kb_access_count = len(body.kb_access) if body.kb_access is not None else await _count_kb_access(key.id, db)

    emit_event(
        "integration.updated",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key.id, "name": key.name},
    )

    return _key_to_response(key, kb_access_count)


# ---------------------------------------------------------------------------
# POST /api/integrations/{id}/revoke
# ---------------------------------------------------------------------------


@router.post("/{integration_id}/revoke")
async def revoke_integration(
    integration_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> IntegrationResponse:
    """Revoke an integration (irreversible). REQ-6.6."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_integration_or_404(integration_id, org.id, db)

    if not key.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Integration is already revoked")

    key.active = False
    await db.commit()

    emit_event(
        "integration.revoked",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key.id, "name": key.name},
    )
    logger.info("Integration revoked", integration_id=key.id, org_id=org.id)

    return _key_to_response(key, await _count_kb_access(key.id, db))


# ---------------------------------------------------------------------------
# DELETE /api/integrations/{id}
# ---------------------------------------------------------------------------


@router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_integration(
    integration_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently delete an integration and its KB access entries."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_integration_or_404(integration_id, org.id, db)

    # CASCADE on FK handles kb_access, but explicit for clarity
    await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))
    await db.execute(
        delete(PartnerAPIKey).where(
            PartnerAPIKey.id == key.id,
            PartnerAPIKey.org_id == org.id,
        )
    )
    await db.commit()

    emit_event(
        "integration.deleted",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key.id, "name": key.name},
    )
    logger.info("Integration deleted", integration_id=key.id, org_id=org.id)
