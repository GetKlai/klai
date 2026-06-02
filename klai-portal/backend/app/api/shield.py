"""Shield API routes.

The first production-shaped slice is intentionally platform-admin only:
Klai staff can mint a browser-extension token, run deterministic compliance
checks, query retrieval, and write privacy-aware Shield logs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db, set_tenant, tenant_scoped_session
from app.core.permissions import UserPermissions, require_platform_admin
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.models.shield import PortalShieldLog, PortalShieldToken
from app.services.shield_compliance import check_compliance
from app.services.shield_tokens import (
    SHIELD_TOKEN_PREFIX,
    generate_shield_token,
    hash_shield_token,
    verify_shield_token,
)
from app.trace import get_trace_headers

logger = structlog.get_logger()
router = APIRouter(tags=["Shield"])

_AUTH_ERROR = {"error": {"type": "authentication_error", "message": "Invalid Shield token"}}
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]


class ShieldTokenCreateRequest(BaseModel):
    name: str = Field(default="Browser extension", min_length=1, max_length=128)
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class ShieldTokenCreateResponse(BaseModel):
    id: str
    name: str
    token: str
    token_prefix: str
    expires_at: datetime | None
    created_at: datetime | None = None


class ShieldTokenListItem(BaseModel):
    id: str
    name: str
    token_prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime | None


class ShieldConfigResponse(BaseModel):
    user: dict[str, Any]
    organization: dict[str, Any]
    compliance: dict[str, Any]
    privacy: dict[str, Any]
    knowledge_bases: list[dict[str, Any]]


class ShieldCheckRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=100_000)
    level: Literal["basic", "extended", "strict"] = "basic"
    check_type: Literal["input", "output"] = "input"


class ShieldLogRequest(BaseModel):
    check_type: Literal["input", "output"] = "input"
    level: Literal["basic", "extended", "strict"] = "basic"
    status: Literal["green", "yellow", "orange", "red"]
    risk_score: int = Field(default=0, ge=0, le=100)
    text: str | None = Field(default=None, max_length=100_000)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    surface: str = Field(default="browser_extension", max_length=32)


class ShieldQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    kb_slugs: list[str] | None = Field(default=None, max_length=25)
    top_k: int = Field(default=6, ge=1, le=15)


@dataclass(frozen=True, slots=True)
class ShieldAuthContext:
    token: PortalShieldToken
    org: PortalOrg
    user: PortalUser

    @property
    def org_id(self) -> int:
        return self.org.id

    @property
    def user_id(self) -> str:
        return self.user.zitadel_user_id


async def _update_last_used(token_id: str, org_id: int) -> None:
    try:
        async with tenant_scoped_session(org_id) as db:
            result = await db.execute(
                text("UPDATE portal_shield_tokens SET last_used_at = now() WHERE id = :id"),
                {"id": token_id},
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                raise RuntimeError(f"portal_shield_tokens last_used_at UPDATE matched 0 rows (id={token_id})")
            await db.commit()
    except Exception:
        logger.exception("shield_token_last_used_update_failed", token_id=token_id, org_id=org_id)


async def get_shield_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ShieldAuthContext:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    plaintext = auth_header[len("Bearer ") :].strip()
    if not plaintext.startswith(SHIELD_TOKEN_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    token_hash = hash_shield_token(plaintext)
    now = datetime.now(UTC)
    token_result = await db.execute(
        select(PortalShieldToken).where(
            PortalShieldToken.token_hash == token_hash,
            PortalShieldToken.revoked_at.is_(None),
            or_(PortalShieldToken.expires_at.is_(None), PortalShieldToken.expires_at > now),
        )
    )
    token = token_result.scalar_one_or_none()
    if token is None or not verify_shield_token(plaintext, token.token_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(
            PortalOrg.id == token.org_id,
            PortalOrg.slug == settings.platform_org_slug,
            PortalUser.zitadel_user_id == token.user_id,
            PortalUser.status == "active",
            PortalUser.role == "admin",
        )
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)
    org, user = row

    await set_tenant(db, org.id)

    task = asyncio.create_task(_update_last_used(token.id, org.id))
    _pending.add(task)
    task.add_done_callback(_pending.discard)

    structlog.contextvars.bind_contextvars(org_id=str(org.id), user_id=user.zitadel_user_id)
    return ShieldAuthContext(token=token, org=org, user=user)


@router.post("/api/app/shield/tokens", response_model=ShieldTokenCreateResponse)
async def create_shield_token(
    body: ShieldTokenCreateRequest,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> ShieldTokenCreateResponse:
    plaintext, token_hash = generate_shield_token()
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=body.expires_in_days) if body.expires_in_days is not None else None
    row = PortalShieldToken(
        org_id=perms.org_id,
        user_id=perms.user_id,
        name=body.name.strip(),
        token_prefix=plaintext[:16],
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ShieldTokenCreateResponse(
        id=row.id,
        name=row.name,
        token=plaintext,
        token_prefix=row.token_prefix,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.get("/api/app/shield/tokens", response_model=list[ShieldTokenListItem])
async def list_shield_tokens(
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> list[ShieldTokenListItem]:
    result = await db.execute(
        select(PortalShieldToken)
        .where(PortalShieldToken.org_id == perms.org_id, PortalShieldToken.user_id == perms.user_id)
        .order_by(PortalShieldToken.created_at.desc())
    )
    return [
        ShieldTokenListItem(
            id=row.id,
            name=row.name,
            token_prefix=row.token_prefix,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            last_used_at=row.last_used_at,
            created_at=row.created_at,
        )
        for row in result.scalars().all()
    ]


@router.delete("/api/app/shield/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_shield_token(
    token_id: str,
    perms: UserPermissions = Depends(require_platform_admin()),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(PortalShieldToken).where(
            PortalShieldToken.id == token_id,
            PortalShieldToken.org_id == perms.org_id,
            PortalShieldToken.user_id == perms.user_id,
            PortalShieldToken.revoked_at.is_(None),
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shield token not found")
    token.revoked_at = datetime.now(UTC)
    await db.commit()


@router.get("/api/shield/config", response_model=ShieldConfigResponse)
async def shield_config(
    auth: ShieldAuthContext = Depends(get_shield_auth),
    db: AsyncSession = Depends(get_db),
) -> ShieldConfigResponse:
    kb_result = await db.execute(
        select(PortalKnowledgeBase).where(PortalKnowledgeBase.org_id == auth.org_id).order_by(PortalKnowledgeBase.name)
    )
    knowledge_bases = [
        {"id": kb.id, "name": kb.name, "slug": kb.slug}
        for kb in kb_result.scalars().all()
    ]
    return ShieldConfigResponse(
        user={
            "id": auth.user.zitadel_user_id,
            "display_name": auth.user.display_name,
            "email": auth.user.email,
            "role": auth.user.role,
        },
        organization={
            "id": auth.org.id,
            "slug": auth.org.slug,
            "name": auth.org.name,
            "platform_admin_only": True,
        },
        compliance={
            "levels": ["basic", "extended", "strict"],
            "default_level": "basic",
            "block_statuses": ["red"],
        },
        privacy={
            "telemetry_level": auth.org.telemetry_level,
            "raw_text_logged": auth.org.telemetry_level == "full",
        },
        knowledge_bases=knowledge_bases,
    )


@router.post("/api/shield/check")
async def shield_check(
    body: ShieldCheckRequest,
    auth: ShieldAuthContext = Depends(get_shield_auth),
) -> dict[str, Any]:
    result = check_compliance(body.text, level=body.level, check_type=body.check_type)
    return {"organization_id": auth.org_id, **result}


@router.post("/api/shield/log")
async def shield_log(
    body: ShieldLogRequest,
    auth: ShieldAuthContext = Depends(get_shield_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if auth.org.telemetry_level == "off":
        return {"stored": False, "telemetry_level": "off"}

    text_preview = None
    if auth.org.telemetry_level == "full" and body.text:
        text_preview = body.text[:200]

    row = PortalShieldLog(
        org_id=auth.org_id,
        user_id=auth.user_id,
        token_id=auth.token.id,
        surface=body.surface,
        check_type=body.check_type,
        level=body.level,
        status=body.status,
        risk_score=body.risk_score,
        text_preview=text_preview,
        warnings=body.warnings[:25],
        sources=body.sources[:25],
        metadata_json=body.metadata,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"stored": True, "id": row.id, "telemetry_level": auth.org.telemetry_level}


@router.post("/api/shield/query")
async def shield_query(
    body: ShieldQueryRequest,
    auth: ShieldAuthContext = Depends(get_shield_auth),
) -> dict[str, Any]:
    retrieval_url = settings.knowledge_retrieve_url
    if not retrieval_url:
        logger.warning("shield_no_retrieval_url", org_id=auth.org_id)
        return {"chunks": [], "sources": [], "retrieval_available": False}

    payload: dict[str, Any] = {
        "query": body.query,
        "raw_query": body.query,
        "org_id": auth.org.zitadel_org_id,
        "user_id": auth.user_id,
        "scope": "both",
        "top_k": body.top_k,
        "conversation_history": [],
        "telemetry_level": "shadow",
        "effective_role": "admin",
    }
    if body.kb_slugs:
        payload["kb_slugs"] = body.kb_slugs

    retrieval_secret = settings.retrieval_api_internal_secret or settings.internal_secret
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{retrieval_url}/retrieve",
                json=payload,
                headers={
                    "X-Internal-Secret": retrieval_secret,
                    "X-Caller-Service": "portal-api",
                    **get_trace_headers(),
                },
            )
            resp.raise_for_status()
            result = resp.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        logger.warning("shield_retrieval_failed", org_id=auth.org_id, error=str(exc))
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Knowledge retrieval unavailable") from exc

    evidence_pack = result.get("evidence_pack") or {}
    chunks = result.get("chunks")
    if chunks is None and isinstance(evidence_pack, dict):
        chunks = evidence_pack.get("items") or []
    return {
        "chunks": chunks or [],
        "sources": result.get("trusted_sources") or result.get("sources") or [],
        "retrieval_available": True,
    }
