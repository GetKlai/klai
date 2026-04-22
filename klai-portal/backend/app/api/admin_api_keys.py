"""Admin API Key management endpoints — SPEC-WIDGET-002.

CRUD for developer-facing partner API keys (`pk_live_...`) scoped to the
caller's org. Auth: Zitadel OIDC session with admin/owner role check.

Split from the previous admin_integrations.py which combined API keys
and widgets. Widgets now live in admin_widgets.py.

No `active` / revoke action — DELETE is the only way to end a key.
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import _get_caller_org, _require_admin
from app.api.bearer import bearer
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.services.events import emit_event
from app.services.partner_keys import generate_partner_key

logger = structlog.get_logger()

router = APIRouter(prefix="/api/api-keys", tags=["API Keys Admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class KbAccessEntry(BaseModel):
    kb_id: int
    access_level: Literal["read", "read_write"]


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=3, max_length=128)
    description: str | None = None
    permissions: dict  # {"chat": bool, "feedback": bool, "knowledge_append": bool}
    kb_access: list[KbAccessEntry]
    rate_limit_rpm: int = Field(default=60, ge=10, le=600)


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    description: str | None
    key_prefix: str
    permissions: dict
    kb_access_count: int
    rate_limit_rpm: int
    last_used_at: str | None
    created_at: str
    created_by: str


class CreateApiKeyResponse(ApiKeyResponse):
    api_key: str  # Full plaintext key — only in create response


class ApiKeyDetailResponse(ApiKeyResponse):
    kb_access: list[dict]  # [{kb_id, kb_name, kb_slug, access_level}]


class UpdateApiKeyRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: dict | None = None
    kb_access: list[KbAccessEntry] | None = None
    rate_limit_rpm: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _key_to_response(key: PartnerAPIKey, kb_access_count: int) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=key.id,
        name=key.name,
        description=key.description,
        key_prefix=key.key_prefix,
        permissions=key.permissions,
        kb_access_count=kb_access_count,
        rate_limit_rpm=key.rate_limit_rpm,
        last_used_at=str(key.last_used_at) if key.last_used_at else None,
        created_at=str(key.created_at),
        created_by=key.created_by,
    )


async def _get_key_or_404(key_id: str, org_id: int, db: AsyncSession) -> PartnerAPIKey:
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.id == key_id,
            PartnerAPIKey.org_id == org_id,
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="API key not found")
    return key


async def _validate_kb_ids(kb_ids: list[int], org_id: int, db: AsyncSession) -> list[PortalKnowledgeBase]:
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
# POST /api/api-keys
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> CreateApiKeyResponse:
    """Create a new partner API key."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    kb_ids = [entry.kb_id for entry in body.kb_access]
    await _validate_kb_ids(kb_ids, org.id, db)

    # Validate: knowledge_append requires at least one read_write KB
    if body.permissions.get("knowledge_append"):
        if not any(e.access_level == "read_write" for e in body.kb_access):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="knowledge_append permission requires at least one KB with read_write access",
            )

    plaintext_key, key_hash = generate_partner_key()
    key_id = str(uuid.uuid4())

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

    await db.refresh(key_row)  # Pre-commit refresh to load server_default columns while tenant context is still set.
    await db.commit()

    emit_event(
        "api_key.created",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"api_key_id": key_id, "name": body.name},
    )
    logger.info("API key created", api_key_id=key_id, org_id=org.id)

    return CreateApiKeyResponse(
        id=key_row.id,
        name=key_row.name,
        description=key_row.description,
        key_prefix=key_row.key_prefix,
        permissions=key_row.permissions,
        kb_access_count=len(body.kb_access),
        rate_limit_rpm=key_row.rate_limit_rpm,
        last_used_at=None,
        created_at=str(key_row.created_at),
        created_by=key_row.created_by,
        api_key=plaintext_key,
    )


# ---------------------------------------------------------------------------
# GET /api/api-keys
# ---------------------------------------------------------------------------


@router.get("")
async def list_api_keys(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    """List all API keys for the caller's org."""
    _caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    result = await db.execute(select(PartnerAPIKey).where(PartnerAPIKey.org_id == org.id))
    keys = result.scalars().all()
    if not keys:
        return []

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
# GET /api/api-keys/{id}
# ---------------------------------------------------------------------------


@router.get("/{key_id}")
async def get_api_key_detail(
    key_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyDetailResponse:
    """Get full detail for a single API key."""
    _caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_key_or_404(key_id, org.id, db)

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

    return ApiKeyDetailResponse(
        id=key.id,
        name=key.name,
        description=key.description,
        key_prefix=key.key_prefix,
        permissions=key.permissions,
        kb_access_count=len(kb_access_list),
        rate_limit_rpm=key.rate_limit_rpm,
        last_used_at=str(key.last_used_at) if key.last_used_at else None,
        created_at=str(key.created_at),
        created_by=key.created_by,
        kb_access=kb_access_list,
    )


# ---------------------------------------------------------------------------
# PATCH /api/api-keys/{id}
# ---------------------------------------------------------------------------


@router.patch("/{key_id}")
async def update_api_key(
    key_id: str,
    body: UpdateApiKeyRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyResponse:
    """Partial update of an API key."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_key_or_404(key_id, org.id, db)

    if body.name is not None:
        key.name = body.name
    if body.description is not None:
        key.description = body.description
    if body.permissions is not None:
        key.permissions = body.permissions
    if body.rate_limit_rpm is not None:
        key.rate_limit_rpm = body.rate_limit_rpm

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
        "api_key.updated",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"api_key_id": key.id, "name": key.name},
    )

    return _key_to_response(key, kb_access_count)


# ---------------------------------------------------------------------------
# DELETE /api/api-keys/{id}
# ---------------------------------------------------------------------------


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently delete an API key and its KB access entries."""
    caller_user_id, org, caller_user = await _get_caller_org(credentials, db)
    _require_admin(caller_user)

    key = await _get_key_or_404(key_id, org.id, db)

    await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))
    await db.execute(
        delete(PartnerAPIKey).where(
            PartnerAPIKey.id == key.id,
            PartnerAPIKey.org_id == org.id,
        )
    )
    await db.commit()

    emit_event(
        "api_key.deleted",
        org_id=org.id,
        user_id=caller_user_id,
        properties={"api_key_id": key.id, "name": key.name},
    )
    logger.info("API key deleted", api_key_id=key.id, org_id=org.id)
