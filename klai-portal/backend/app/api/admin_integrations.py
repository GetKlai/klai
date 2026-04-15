"""Admin integration management endpoints.

SPEC-API-001 REQ-6.1 through REQ-6.7:
- CRUD for partner API keys ("integrations") scoped to caller's org
- Auth: Zitadel OIDC session with admin role check
- Product events for create, update, revoke actions
"""

from __future__ import annotations

import json
import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
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


async def _ensure_tenant(db: AsyncSession, org_id: int) -> None:
    """Set RLS tenant on the current connection.

    Must be called before every query block because the async connection pool
    may hand out a different connection than the one set_tenant() used in
    _get_caller_org(). See: RLS + async SQLAlchemy pitfall in CodeIndex memory.
    """
    await db.execute(
        text("SELECT set_config('app.current_org_id', :oid, false)"),
        {"oid": str(org_id)},
    )


async def _get_integration_or_404(integration_id: str, org_id: int, db: AsyncSession) -> PartnerAPIKey:
    """Fetch integration scoped to org, raise 404 if not found."""
    await _ensure_tenant(db, org_id)
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


async def _validate_kb_ids(kb_ids: list[int], org_id: int, db: AsyncSession) -> list[dict]:
    """Validate that all kb_ids belong to the org. Returns matching KB dicts.

    Explicitly sets tenant context before querying because the connection
    from the pool may not have app.current_org_id set yet (async session
    can checkout a different connection than set_tenant used).
    """
    if not kb_ids:
        return []
    # Ensure tenant is set on THIS connection right before the query
    await db.execute(
        text("SELECT set_config('app.current_org_id', :org_id, false)"),
        {"org_id": str(org_id)},
    )
    result = await db.execute(
        text("SELECT id, name, slug FROM portal_knowledge_bases WHERE id = ANY(:kb_ids) AND org_id = :org_id"),
        {"kb_ids": kb_ids, "org_id": org_id},
    )
    found_rows = result.fetchall()
    found_ids = {row.id for row in found_rows}
    missing = set(kb_ids) - found_ids
    if missing:
        logger.warning("KB IDs not found", missing=sorted(missing), org_id=org_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Knowledge base IDs not found in your organisation: {sorted(missing)}",
        )
    return [{"id": row.id, "name": row.name, "slug": row.slug} for row in found_rows]


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

    # Ensure tenant is set for RLS INSERT policy
    key_id = str(uuid.uuid4())
    await db.execute(
        text("SELECT set_config('app.current_org_id', :org_id, false)"),
        {"org_id": str(org.id)},
    )
    await db.execute(
        text(
            "INSERT INTO partner_api_keys "
            "(id, org_id, name, description, key_prefix, key_hash, permissions, "
            "rate_limit_rpm, active, created_by, created_at) "
            "VALUES (:id, :org_id, :name, :description, :key_prefix, :key_hash, "
            "CAST(:permissions AS jsonb), :rate_limit_rpm, true, :created_by, now())"
        ),
        {
            "id": key_id,
            "org_id": org.id,
            "name": body.name,
            "description": body.description,
            "key_prefix": key_prefix,
            "key_hash": key_hash,
            "permissions": json.dumps(body.permissions),
            "rate_limit_rpm": body.rate_limit_rpm,
            "created_by": caller_user_id,
        },
    )

    # Create KB access rows
    for entry in body.kb_access:
        await db.execute(
            text(
                "INSERT INTO partner_api_key_kb_access "
                "(partner_api_key_id, kb_id, access_level) "
                "VALUES (:key_id, :kb_id, :access_level)"
            ),
            {
                "key_id": key_id,
                "kb_id": entry.kb_id,
                "access_level": entry.access_level,
            },
        )

    await db.commit()

    # Emit product event (REQ-6.7)
    emit_event(
        "integration.created",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key_id, "name": body.name},
    )

    logger.info(
        "Integration created",
        integration_id=key_id,
        org_id=org.id,
    )

    # Fetch the created row for the response timestamp
    created_row = await db.execute(
        text("SELECT created_at FROM partner_api_keys WHERE id = :id"),
        {"id": key_id},
    )
    created_at = str(created_row.scalar())

    return CreateIntegrationResponse(
        id=key_id,
        name=body.name,
        description=body.description,
        key_prefix=key_prefix,
        permissions=body.permissions,
        active=True,
        kb_access_count=len(body.kb_access),
        rate_limit_rpm=body.rate_limit_rpm,
        last_used_at=None,
        created_at=created_at,
        created_by=caller_user_id,
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

    await _ensure_tenant(db, org.id)
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

        # Delete existing rows — raw SQL to bypass RLS
        await db.execute(
            text("DELETE FROM partner_api_key_kb_access WHERE partner_api_key_id = :key_id"),
            {"key_id": key.id},
        )

        # Insert new rows — raw SQL to bypass RLS
        for entry in body.kb_access:
            await db.execute(
                text(
                    "INSERT INTO partner_api_key_kb_access (partner_api_key_id, kb_id, access_level) "
                    "VALUES (:key_id, :kb_id, :access_level)"
                ),
                {"key_id": key.id, "kb_id": entry.kb_id, "access_level": entry.access_level},
            )
        kb_access_count = len(body.kb_access)

    await db.commit()

    # If we didn't update kb_access, count the existing ones
    if kb_access_count is None:
        await _ensure_tenant(db, org.id)
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
    await _ensure_tenant(db, org.id)
    count_result = await db.execute(
        select(func.count())
        .select_from(PartnerApiKeyKbAccess)
        .where(PartnerApiKeyKbAccess.partner_api_key_id == key.id)
    )
    kb_access_count = count_result.scalar() or 0

    return _key_to_response(key, kb_access_count)


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

    await _ensure_tenant(db, org.id)
    await db.execute(
        text("DELETE FROM partner_api_key_kb_access WHERE partner_api_key_id = :kid"),
        {"kid": key.id},
    )
    await db.execute(
        text("DELETE FROM partner_api_keys WHERE id = :id AND org_id = :oid"),
        {"id": key.id, "oid": org.id},
    )
    await db.commit()

    emit_event(
        "integration.deleted",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"integration_id": key.id, "name": key.name},
    )

    logger.info("Integration deleted", integration_id=key.id, org_id=org.id)
