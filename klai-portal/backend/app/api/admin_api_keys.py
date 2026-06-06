"""Admin API Key management endpoints — SPEC-WIDGET-002 + SPEC-PORTAL-EXTENSIONS-UNIFY-001.

CRUD for developer-facing partner API keys (`pk_live_...`) scoped to the
caller's org. Auth: Zitadel OIDC session with admin role check AND the
`partner_api` platform-unlock (SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 1,
2026-05-12). Tenants without `partner_api` in `platform_unlocked_features`
get 403 on every endpoint — the feature is opt-in per tenant via Klai
platform admin (`/api/admin/extensions` or
`/api/admin/orgs/{slug}/platform-unlocks`).

Split from the previous admin_integrations.py which combined API keys
and widgets. Widgets now live in admin_widgets.py and use the same
double-gate pattern (admin role + platform-unlock).

No `active` / revoke action — DELETE is the only way to end a key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import (
    ProfileRole,
    UserPermissions,
    get_caller_at_least,
    require_platform_unlocked,
)
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.services.events import emit_event
from app.services.partner_keys import generate_partner_key

logger = structlog.get_logger()

router = APIRouter(prefix="/api/admin/api-keys", tags=["API Keys Admin"])


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
    rotated_from_key_id: str | None = None
    rotated_to_key_id: str | None = None
    rotation_started_at: str | None = None


class CreateApiKeyResponse(ApiKeyResponse):
    api_key: str  # Full plaintext key — only in create response


class RotateApiKeyResponse(CreateApiKeyResponse):
    old_key_id: str


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
    rotated_from_key_id = getattr(key, "rotated_from_key_id", None)
    rotated_to_key_id = getattr(key, "rotated_to_key_id", None)
    rotation_started_at = getattr(key, "rotation_started_at", None)

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
        rotated_from_key_id=rotated_from_key_id if isinstance(rotated_from_key_id, str) else None,
        rotated_to_key_id=rotated_to_key_id if isinstance(rotated_to_key_id, str) else None,
        rotation_started_at=str(rotation_started_at) if isinstance(rotation_started_at, datetime) else None,
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


def _personal_kbs_not_owned_by_user(kbs: list[PortalKnowledgeBase], user_id: str) -> list[int]:
    return [kb.id for kb in kbs if kb.owner_type == "user" and kb.owner_user_id != user_id]


def _has_personal_kb(kbs: list[PortalKnowledgeBase]) -> bool:
    return any(kb.owner_type == "user" for kb in kbs)


def _kb_access_response_entry(
    access: PartnerApiKeyKbAccess,
    kb: PortalKnowledgeBase,
    viewer_user_id: str,
) -> dict:
    if kb.owner_type == "user" and kb.owner_user_id != viewer_user_id:
        return {
            "kb_id": access.kb_id,
            "kb_name": "Personal knowledge base",
            "kb_slug": None,
            "access_level": access.access_level,
        }
    return {
        "kb_id": access.kb_id,
        "kb_name": kb.name,
        "kb_slug": kb.slug,
        "access_level": access.access_level,
    }


async def _validate_kb_ids(
    kb_ids: list[int],
    org_id: int,
    user_id: str,
    db: AsyncSession,
) -> list[PortalKnowledgeBase]:
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
    other_personal = _personal_kbs_not_owned_by_user(list(found_kbs), user_id)
    if other_personal:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Personal knowledge base IDs are not owned by the caller: {sorted(other_personal)}",
        )
    return list(found_kbs)


async def _count_kb_access(key_id: str, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(PartnerApiKeyKbAccess)
        .where(PartnerApiKeyKbAccess.partner_api_key_id == key_id)
    )
    return result.scalar() or 0


def _rotated_key_name(name: str, now: datetime) -> str:
    suffix = f" (rotated {now.date().isoformat()})"
    return f"{name[: 128 - len(suffix)]}{suffix}"


# ---------------------------------------------------------------------------
# POST /api/api-keys
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: CreateApiKeyRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> CreateApiKeyResponse:
    """Create a new partner API key."""
    kb_ids = [entry.kb_id for entry in body.kb_access]
    await _validate_kb_ids(kb_ids, perms.org_id, perms.user_id, db)

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
        org_id=perms.org_id,
        name=body.name,
        description=body.description,
        key_prefix=plaintext_key[:12],
        key_hash=key_hash,
        permissions=body.permissions,
        rate_limit_rpm=body.rate_limit_rpm,
        created_by=perms.user_id,
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

    await db.flush()  # Promote key_row to persistent so refresh() can run.
    await db.refresh(key_row)  # Pre-commit refresh to load server_default columns while tenant context is still set.
    await db.commit()

    emit_event(
        "api_key.created",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"api_key_id": key_id, "name": body.name},
    )
    logger.info("API key created", api_key_id=key_id, org_id=perms.org_id)

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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKeyResponse]:
    """List all API keys for the caller's org."""
    result = await db.execute(select(PartnerAPIKey).where(PartnerAPIKey.org_id == perms.org_id))
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
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyDetailResponse:
    """Get full detail for a single API key."""
    key = await _get_key_or_404(key_id, perms.org_id, db)

    kb_result = await db.execute(
        select(PartnerApiKeyKbAccess, PortalKnowledgeBase)
        .join(PortalKnowledgeBase, PartnerApiKeyKbAccess.kb_id == PortalKnowledgeBase.id)
        .where(PartnerApiKeyKbAccess.partner_api_key_id == key.id)
    )
    kb_access_list = [_kb_access_response_entry(access, kb, perms.user_id) for access, kb in kb_result]
    rotated_from_key_id = getattr(key, "rotated_from_key_id", None)
    rotated_to_key_id = getattr(key, "rotated_to_key_id", None)
    rotation_started_at = getattr(key, "rotation_started_at", None)

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
        rotated_from_key_id=rotated_from_key_id if isinstance(rotated_from_key_id, str) else None,
        rotated_to_key_id=rotated_to_key_id if isinstance(rotated_to_key_id, str) else None,
        rotation_started_at=str(rotation_started_at) if isinstance(rotation_started_at, datetime) else None,
        kb_access=kb_access_list,
    )


# ---------------------------------------------------------------------------
# POST /api/api-keys/{id}/rotate
# ---------------------------------------------------------------------------


@router.post("/{key_id}/rotate", status_code=status.HTTP_201_CREATED)
async def rotate_api_key(
    key_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> RotateApiKeyResponse:
    """Create a replacement key with the same permissions and KB access.

    The old key remains valid until the admin deletes it, giving customers a
    zero-downtime rotation window.
    """
    source_key = await _get_key_or_404(key_id, perms.org_id, db)
    if source_key.rotated_to_key_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="API key already has a pending rotation",
        )

    kb_result = await db.execute(
        select(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == source_key.id)
    )
    kb_rows = list(kb_result.scalars().all())
    cloned_kbs = await _validate_kb_ids([row.kb_id for row in kb_rows], perms.org_id, perms.user_id, db)
    if _has_personal_kb(cloned_kbs) and source_key.created_by != perms.user_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Personal knowledge base access can only be rotated by the API key creator",
        )

    now = datetime.now(UTC)
    plaintext_key, key_hash = generate_partner_key()
    new_key_id = str(uuid.uuid4())

    new_key = PartnerAPIKey(
        id=new_key_id,
        org_id=perms.org_id,
        name=_rotated_key_name(source_key.name, now),
        description=source_key.description,
        key_prefix=plaintext_key[:12],
        key_hash=key_hash,
        permissions=dict(source_key.permissions),
        rate_limit_rpm=source_key.rate_limit_rpm,
        created_by=perms.user_id,
        rotated_from_key_id=source_key.id,
    )
    db.add(new_key)
    await db.flush()

    for row in kb_rows:
        db.add(
            PartnerApiKeyKbAccess(
                partner_api_key_id=new_key_id,
                kb_id=row.kb_id,
                access_level=row.access_level,
            )
        )

    source_key.rotated_to_key_id = new_key_id
    source_key.rotation_started_at = now

    await db.flush()
    await db.refresh(new_key)
    await db.commit()

    emit_event(
        "api_key.rotated",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={
            "api_key_id": source_key.id,
            "rotated_to_key_id": new_key_id,
            "name": source_key.name,
        },
    )
    logger.info("API key rotated", api_key_id=source_key.id, rotated_to_key_id=new_key_id, org_id=perms.org_id)

    return RotateApiKeyResponse(
        id=new_key.id,
        old_key_id=source_key.id,
        name=new_key.name,
        description=new_key.description,
        key_prefix=new_key.key_prefix,
        permissions=new_key.permissions,
        kb_access_count=len(kb_rows),
        rate_limit_rpm=new_key.rate_limit_rpm,
        last_used_at=None,
        created_at=str(new_key.created_at),
        created_by=new_key.created_by,
        rotated_from_key_id=source_key.id,
        rotated_to_key_id=None,
        rotation_started_at=None,
        api_key=plaintext_key,
    )


# ---------------------------------------------------------------------------
# PATCH /api/api-keys/{id}
# ---------------------------------------------------------------------------


@router.patch("/{key_id}")
async def update_api_key(
    key_id: str,
    body: UpdateApiKeyRequest,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyResponse:
    """Partial update of an API key."""
    key = await _get_key_or_404(key_id, perms.org_id, db)

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
        selected_kbs = await _validate_kb_ids(kb_ids, perms.org_id, perms.user_id, db)
        if _has_personal_kb(selected_kbs) and key.created_by != perms.user_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Personal knowledge base access can only be added to API keys created by the caller",
            )

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
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"api_key_id": key.id, "name": key.name},
    )

    return _key_to_response(key, kb_access_count)


# ---------------------------------------------------------------------------
# DELETE /api/api-keys/{id}
# ---------------------------------------------------------------------------


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: str,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    _platform: UserPermissions = Depends(require_platform_unlocked("partner_api")),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently delete an API key and its KB access entries."""
    key = await _get_key_or_404(key_id, perms.org_id, db)

    await db.execute(delete(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key.id))
    await db.execute(
        delete(PartnerAPIKey).where(
            PartnerAPIKey.id == key.id,
            PartnerAPIKey.org_id == perms.org_id,
        )
    )
    await db.commit()

    emit_event(
        "api_key.deleted",
        org_id=perms.org_id,
        user_id=perms.user_id,
        properties={"api_key_id": key.id, "name": key.name},
    )
    logger.info("API key deleted", api_key_id=key.id, org_id=perms.org_id)
