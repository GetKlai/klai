"""Admin integration management endpoints.

SPEC-API-001 REQ-6.1 through REQ-6.7:
- CRUD for partner API keys ("integrations") scoped to caller's org
- Auth: Zitadel OIDC session with admin role check
- Product events for create, update, revoke actions
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
    """Map a PartnerAPIKey row to IntegrationResponse."""
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
    """Fetch integration scoped to org, raise 404 if not found."""
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.id == integration_id,
            PartnerAPIKey.org_id == org_id,
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )
    return key


async def _validate_kb_ids(kb_ids: list[int], org_id: int, db: AsyncSession) -> list[PortalKnowledgeBase]:
    """Validate that all kb_ids belong to the org. Returns matching KB rows."""
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge base IDs not found in your organisation: {sorted(missing)}",
        )
    return list(found_kbs)


# ---------------------------------------------------------------------------
# TASK-012: POST /api/integrations
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
        has_rw = any(entry.access_level == "read_write" for entry in body.kb_access)
        if not has_rw:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="knowledge_append permission requires at least one KB with read_write access",
            )

    # Generate key
    plaintext_key, key_hash = generate_partner_key()
    key_prefix = plaintext_key[:12]

    # Create PartnerAPIKey row
    key_id = str(uuid.uuid4())
    key_row = PartnerAPIKey(
        id=key_id,
        org_id=org.id,
        name=body.name,
        description=body.description,
        key_prefix=key_prefix,
        key_hash=key_hash,
        permissions=body.permissions,
        rate_limit_rpm=body.rate_limit_rpm,
        created_by=caller_user_id,
    )
    db.add(key_row)
    await db.flush()  # Get the generated ID

    # Create KB access rows
    for entry in body.kb_access:
        db.add(
            PartnerApiKeyKbAccess(
                partner_api_key_id=key_row.id,
                kb_id=entry.kb_id,
                access_level=entry.access_level,
            )
        )

    await db.commit()

    # Emit product event (REQ-6.7)
    emit_event(
        "integration.created",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key_row.id, "name": body.name},
    )

    logger.info(
        "Integration created",
        integration_id=key_row.id,
        org_id=org.id,
    )

    return CreateIntegrationResponse(
        id=key_row.id,
        name=key_row.name,
        description=key_row.description,
        key_prefix=key_prefix,
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
# TASK-012: GET /api/integrations
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

    # Load KB access counts for all keys in one query
    key_ids = [k.id for k in keys]
    kb_result = await db.execute(
        select(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id.in_(key_ids))
    )
    kb_rows = kb_result.scalars().all()

    # Count per key
    kb_counts: dict[str, int] = {}
    for row in kb_rows:
        kb_counts[row.partner_api_key_id] = kb_counts.get(row.partner_api_key_id, 0) + 1

    return [_key_to_response(k, kb_counts.get(k.id, 0)) for k in keys]


# ---------------------------------------------------------------------------
# TASK-013: GET /api/integrations/{id}
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

    # Load KB access entries
    kb_access_result = await db.execute(
        select(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id)
    )
    kb_access_rows = kb_access_result.scalars().all()

    # Load KB names
    kb_ids = [row.kb_id for row in kb_access_rows]
    kb_details: dict[int, PortalKnowledgeBase] = {}
    if kb_ids:
        kb_result = await db.execute(select(PortalKnowledgeBase).where(PortalKnowledgeBase.id.in_(kb_ids)))
        for kb in kb_result.scalars().all():
            kb_details[kb.id] = kb

    kb_access_list = [
        {
            "kb_id": row.kb_id,
            "kb_name": kb_details[row.kb_id].name if row.kb_id in kb_details else "Unknown",
            "kb_slug": kb_details[row.kb_id].slug if row.kb_id in kb_details else "",
            "access_level": row.access_level,
        }
        for row in kb_access_rows
    ]

    return IntegrationDetailResponse(
        id=key.id,
        name=key.name,
        description=key.description,
        key_prefix=key.key_prefix,
        permissions=key.permissions,
        active=key.active,
        kb_access_count=len(kb_access_rows),
        rate_limit_rpm=key.rate_limit_rpm,
        last_used_at=str(key.last_used_at) if key.last_used_at else None,
        created_at=str(key.created_at),
        created_by=key.created_by,
        kb_access=kb_access_list,
    )


# ---------------------------------------------------------------------------
# TASK-013: PATCH /api/integrations/{id}
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

    # Cannot update a revoked key
    if not key.active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update a revoked integration",
        )

    # Apply partial updates
    if body.name is not None:
        key.name = body.name
    if body.description is not None:
        key.description = body.description
    if body.permissions is not None:
        key.permissions = body.permissions
    if body.rate_limit_rpm is not None:
        key.rate_limit_rpm = body.rate_limit_rpm

    kb_access_count: int | None = None

    # Atomic KB access replacement (REQ-6.5)
    if body.kb_access is not None:
        kb_ids = [entry.kb_id for entry in body.kb_access]
        await _validate_kb_ids(kb_ids, org.id, db)

        # Delete existing rows
        await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))

        # Insert new rows
        for entry in body.kb_access:
            db.add(
                PartnerApiKeyKbAccess(
                    partner_api_key_id=key.id,
                    kb_id=entry.kb_id,
                    access_level=entry.access_level,
                )
            )
        kb_access_count = len(body.kb_access)

    await db.commit()

    # If we didn't update kb_access, count the existing ones
    if kb_access_count is None:
        count_result = await db.execute(
            select(func.count())
            .select_from(PartnerApiKeyKbAccess)
            .where(PartnerApiKeyKbAccess.partner_api_key_id == key.id)
        )
        kb_access_count = count_result.scalar() or 0

    # Emit product event (REQ-6.7)
    emit_event(
        "integration.updated",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key.id, "name": key.name},
    )

    return _key_to_response(key, kb_access_count)


# ---------------------------------------------------------------------------
# TASK-013: POST /api/integrations/{id}/revoke
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integration is already revoked",
        )

    key.active = False
    await db.commit()

    # Emit product event (REQ-6.7)
    emit_event(
        "integration.revoked",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key.id, "name": key.name},
    )

    logger.info(
        "Integration revoked",
        integration_id=key.id,
        org_id=org.id,
    )

    # Count KB access for response
    count_result = await db.execute(
        select(func.count())
        .select_from(PartnerApiKeyKbAccess)
        .where(PartnerApiKeyKbAccess.partner_api_key_id == key.id)
    )
    kb_access_count = count_result.scalar() or 0

    return _key_to_response(key, kb_access_count)
