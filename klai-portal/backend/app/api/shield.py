"""Shield API routes.

The first production-shaped slice is intentionally platform-admin only:
Klai staff can mint a browser-extension token, run deterministic compliance
checks, query retrieval, and write privacy-aware Shield logs.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse, StreamingResponse

from app.core.config import settings
from app.core.database import get_db, set_tenant, tenant_scoped_session
from app.core.permissions import ProfileRole, UserPermissions, require_platform_admin, resolve_user_permissions
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.models.shield import PortalShieldAuthCode, PortalShieldLog, PortalShieldToken
from app.services.shield_compliance import check_compliance
from app.services.shield_tokens import (
    SHIELD_AUTH_CODE_PREFIX,
    SHIELD_TOKEN_PREFIX,
    generate_shield_auth_code,
    generate_shield_token,
    hash_shield_token,
    verify_shield_auth_code,
    verify_shield_token,
)
from app.trace import get_trace_headers

logger = structlog.get_logger()
router = APIRouter(tags=["Shield"])

_AUTH_ERROR = {"error": {"type": "authentication_error", "message": "Invalid Shield token"}}
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]
_EXTENSION_DIR = Path(__file__).resolve().parent.parent / "static" / "shield-extension"
_EXTENSION_ZIP_NAME = "klai-shield-extension.zip"
_EXTENSION_AUTH_CODE_TTL = timedelta(minutes=10)
_EXTENSION_TOKEN_TTL = timedelta(days=30)


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


class ShieldExtensionInfoResponse(BaseModel):
    name: str
    version: str
    download_url: str
    platform_admin_only: bool = True


class ShieldExtensionExchangeRequest(BaseModel):
    code: str = Field(..., min_length=len(SHIELD_AUTH_CODE_PREFIX) + 16, max_length=128)


class ShieldExtensionExchangeResponse(BaseModel):
    success: bool = True
    token: str
    expires_at: datetime
    user: dict[str, Any]
    organization: dict[str, Any]


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


def _build_extension_zip_bytes(extension_dir: Path) -> bytes:
    manifest = extension_dir / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError("Shield extension manifest not found")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(extension_dir.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, Path("klai-shield-extension") / path.relative_to(extension_dir))
    return buffer.getvalue()


def _append_query(url: str, params: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update(params)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
    )


def _validated_extension_redirect_uri(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc.endswith(".chromiumapp.org"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid extension redirect_uri")
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _extension_error_redirect(redirect_uri: str, error: str) -> RedirectResponse:
    return RedirectResponse(url=_append_query(redirect_uri, {"error": error}), status_code=302)


def _encode_redirect_uri(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_redirect_uri(value: str) -> str:
    padded = f"{value}{'=' * (-len(value) % 4)}"
    try:
        return base64.urlsafe_b64decode(padded.encode()).decode()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid extension redirect_uri") from exc


def _extension_login_return_to(request: Request, redirect_uri: str) -> str:
    encoded = _encode_redirect_uri(redirect_uri)
    return f"{request.url.path}?redirect_uri_b64={encoded}"


def _resolve_extension_redirect_uri(redirect_uri: str | None, redirect_uri_b64: str | None) -> str:
    if redirect_uri_b64:
        return _validated_extension_redirect_uri(_decode_redirect_uri(redirect_uri_b64))
    if redirect_uri:
        return _validated_extension_redirect_uri(redirect_uri)
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing extension redirect_uri")


async def _load_platform_admin_user(
    db: AsyncSession,
    *,
    org_id: int | None,
    user_id: str,
) -> tuple[PortalOrg, PortalUser] | None:
    if org_id is None:
        return None

    perms = await resolve_user_permissions(user_id, db, org_id=org_id)
    if (
        perms is None
        or not perms.is_platform_admin
        or perms.effective_role != ProfileRole.ADMIN
        or perms.status != "active"
    ):
        return None

    result = await db.execute(
        select(PortalOrg, PortalUser)
        .join(PortalUser, PortalUser.org_id == PortalOrg.id)
        .where(
            PortalOrg.id == org_id,
            PortalOrg.slug == settings.platform_org_slug,
            PortalUser.zitadel_user_id == user_id,
            PortalUser.status == "active",
            PortalUser.role == "admin",
        )
    )
    return result.one_or_none()


@router.get("/api/app/shield/extension/login")
async def shield_extension_login(
    request: Request,
    redirect_uri: str | None = None,
    redirect_uri_b64: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    safe_redirect_uri = _resolve_extension_redirect_uri(redirect_uri, redirect_uri_b64)
    session = getattr(request.state, "session", None)
    if session is None:
        return_to = _extension_login_return_to(request, safe_redirect_uri)
        login_query = urllib.parse.urlencode({"return_to": return_to})
        return RedirectResponse(url=f"/api/auth/oidc/start?{login_query}", status_code=302)

    row = await _load_platform_admin_user(
        db,
        org_id=getattr(session, "org_id", None),
        user_id=getattr(session, "zitadel_user_id", ""),
    )
    if row is None:
        return _extension_error_redirect(safe_redirect_uri, "platform_admin_required")

    org, user = row
    await set_tenant(db, org.id)

    code, code_hash = generate_shield_auth_code()
    now = datetime.now(UTC)
    db.add(
        PortalShieldAuthCode(
            org_id=org.id,
            user_id=user.zitadel_user_id,
            code_hash=code_hash,
            expires_at=now + _EXTENSION_AUTH_CODE_TTL,
        )
    )
    await db.commit()
    return RedirectResponse(url=_append_query(safe_redirect_uri, {"code": code}), status_code=302)


@router.post("/api/app/shield/extension/exchange", response_model=ShieldExtensionExchangeResponse)
async def exchange_shield_extension_code(
    body: ShieldExtensionExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> ShieldExtensionExchangeResponse:
    plaintext = body.code.strip()
    if not plaintext.startswith(SHIELD_AUTH_CODE_PREFIX):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    now = datetime.now(UTC)
    code_hash = hash_shield_token(plaintext)
    result = await db.execute(
        select(PortalShieldAuthCode)
        .where(
            PortalShieldAuthCode.code_hash == code_hash,
            PortalShieldAuthCode.consumed_at.is_(None),
            PortalShieldAuthCode.expires_at > now,
        )
        .with_for_update()
    )
    auth_code = result.scalar_one_or_none()
    if auth_code is None or not verify_shield_auth_code(plaintext, auth_code.code_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    row = await _load_platform_admin_user(db, org_id=auth_code.org_id, user_id=auth_code.user_id)
    if row is None:
        auth_code.consumed_at = now
        await db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: platform admin required")

    org, user = row
    await set_tenant(db, org.id)

    token, token_hash = generate_shield_token()
    expires_at = now + _EXTENSION_TOKEN_TTL
    db.add(
        PortalShieldToken(
            org_id=org.id,
            user_id=user.zitadel_user_id,
            name="Browser extension login",
            token_prefix=token[:16],
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    auth_code.consumed_at = now
    await db.commit()

    return ShieldExtensionExchangeResponse(
        token=token,
        expires_at=expires_at,
        user={
            "id": user.zitadel_user_id,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
        },
        organization={
            "id": org.id,
            "slug": org.slug,
            "name": org.name,
        },
    )


@router.get("/api/app/shield/extension", response_model=ShieldExtensionInfoResponse)
async def shield_extension_info(
    _perms: UserPermissions = Depends(require_platform_admin()),
) -> ShieldExtensionInfoResponse:
    return ShieldExtensionInfoResponse(
        name="Klai Shield",
        version="0.2.0",
        download_url="/api/app/shield/extension.zip",
    )


@router.get("/api/app/shield/extension.zip")
async def download_shield_extension(
    _perms: UserPermissions = Depends(require_platform_admin()),
) -> StreamingResponse:
    try:
        data = _build_extension_zip_bytes(_EXTENSION_DIR)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shield extension package not found") from exc

    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_EXTENSION_ZIP_NAME}"',
            "Cache-Control": "no-store",
        },
    )


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
    knowledge_bases = [{"id": kb.id, "name": kb.name, "slug": kb.slug} for kb in kb_result.scalars().all()]
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
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Knowledge retrieval unavailable"
        ) from exc

    evidence_pack = result.get("evidence_pack") or {}
    chunks = result.get("chunks")
    if chunks is None and isinstance(evidence_pack, dict):
        chunks = evidence_pack.get("items") or []
    return {
        "chunks": chunks or [],
        "sources": result.get("trusted_sources") or result.get("sources") or [],
        "retrieval_available": True,
    }
