"""Partner API authentication and authorization dependencies.

SPEC-API-001 REQ-2.1 through REQ-2.6 and SPEC-WIDGET-002:
- Extract Bearer pk_... token from Authorization header
- SHA-256 hash lookup in partner_api_keys (no `active` filter — DELETE is
  the only way to end a key, per SPEC-WIDGET-002)
- Rate limit enforcement via Redis sliding window
- Non-blocking last_used_at update
- Error messages never distinguish not-found from deleted (no enumeration)
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import jwt
import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db, set_tenant
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.models.portal import PortalOrg
from app.services.partner_keys import verify_partner_key
from app.services.partner_rate_limit import check_rate_limit
from app.services.redis_client import get_redis_pool
from app.services.widget_auth import decode_session_token

logger = structlog.get_logger()

_AUTH_ERROR = {"error": {"type": "authentication_error", "message": "Invalid API key"}}

# Hold references to fire-and-forget tasks to prevent GC (same pattern as app.services.events)
_pending: set[asyncio.Task] = set()  # type: ignore[type-arg]


@dataclass
class PartnerAuthContext:
    """Resolved partner auth state passed to endpoint handlers."""

    key_id: str  # UUID as string
    org_id: int  # portal org integer id
    zitadel_org_id: str  # for retrieval-api calls
    permissions: dict  # {"chat": bool, "feedback": bool, "knowledge_append": bool}
    kb_access: dict[int, str]  # {kb_id: access_level} from junction table
    rate_limit_rpm: int


async def _update_last_used(key_id: str, org_id: int) -> None:
    """Update last_used_at timestamp (fire-and-forget, independent session).

    Uses raw SQL with explicit set_config because this runs as an asyncio.create_task
    on a fresh session — no tenant context from the request is available.
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("SELECT set_config('app.current_org_id', :oid, false)"),
                {"oid": str(org_id)},
            )
            await db.execute(
                text("UPDATE partner_api_keys SET last_used_at = now() WHERE id = :id"),
                {"id": key_id},
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to update last_used_at", partner_key_id=key_id)


async def _auth_via_session_token(token: str, db: AsyncSession) -> PartnerAuthContext:
    """Authenticate via widget JWT session token.

    # @MX:ANCHOR: Widget session token auth path
    # @MX:REASON: Called from get_partner_key for non-pk_live_ tokens; must be secure

    Raises 401 for invalid/expired tokens.
    Raises 401 if WIDGET_JWT_SECRET is not configured.

    Args:
        token: Raw Bearer token value (not starting with pk_live_)
        db: Database session (used to load org for set_tenant)

    Returns:
        PartnerAuthContext built from JWT claims
    """
    if not settings.widget_jwt_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    try:
        payload = decode_session_token(token, settings.widget_jwt_secret)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR) from exc

    org_id: int = payload.get("org_id", 0)
    wgt_id: str = payload.get("wgt_id", "")
    kb_ids: list[int] = payload.get("kb_ids", [])

    if not org_id or not wgt_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    # Load org for zitadel_org_id and set RLS tenant
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)
    await set_tenant(db, org.id)

    # Build kb_access with read-only access for all JWT kb_ids
    kb_access = {kb_id: "read" for kb_id in kb_ids}

    # Apply rate limiting using wgt_id as the key (same limit as pk_live_ path)
    _SESSION_RATE_LIMIT_RPM = 60
    redis_pool = await get_redis_pool()
    if redis_pool:
        allowed, retry_after = await check_rate_limit(redis_pool, wgt_id, _SESSION_RATE_LIMIT_RPM)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
                headers={"Retry-After": str(retry_after)},
            )

    structlog.contextvars.bind_contextvars(
        wgt_id=wgt_id,
        org_id=org_id,
    )

    return PartnerAuthContext(
        key_id=wgt_id,
        org_id=org_id,
        zitadel_org_id=org.zitadel_org_id,
        permissions={"chat": True, "feedback": False, "knowledge_append": False},
        kb_access=kb_access,
        rate_limit_rpm=_SESSION_RATE_LIMIT_RPM,
    )


async def get_partner_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PartnerAuthContext:
    """FastAPI dependency: authenticate partner API key and return context.

    Extracts Bearer token, validates via SHA-256 hash lookup,
    enforces rate limits, and schedules last_used_at update.
    """
    # Step 1: Extract token from Authorization header
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    token = auth_header[len("Bearer ") :]

    # Step 2a: Try JWT session token if not a pk_live_ key
    if not token.startswith("pk_live_"):
        return await _auth_via_session_token(token, db)

    # Step 3: Compute hash and look up key (SPEC-WIDGET-002: no active filter)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(PartnerAPIKey).where(PartnerAPIKey.key_hash == key_hash)
    )
    key_row = result.scalar_one_or_none()

    if key_row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    # Step 4: Verify key (constant-time comparison)
    if not verify_partner_key(token, key_row.key_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    # Step 5: Load KB access entries
    kb_result = await db.execute(
        select(PartnerApiKeyKbAccess).where(PartnerApiKeyKbAccess.partner_api_key_id == key_row.id)
    )
    kb_rows = kb_result.scalars().all()
    kb_access = {row.kb_id: row.access_level for row in kb_rows}

    # Step 6: Resolve org_id -> zitadel_org_id and set tenant for downstream ORM queries
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == key_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)
    await set_tenant(db, org.id)

    # Step 7: Check rate limit
    redis_pool = await get_redis_pool()
    if redis_pool:
        allowed, retry_after = await check_rate_limit(redis_pool, key_row.id, key_row.rate_limit_rpm)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
                headers={"Retry-After": str(retry_after)},
            )

    # Step 8: Schedule last_used_at update (non-blocking)
    task = asyncio.create_task(_update_last_used(key_row.id, key_row.org_id))
    _pending.add(task)
    task.add_done_callback(_pending.discard)

    # Bind structured log context
    structlog.contextvars.bind_contextvars(
        partner_key_id=key_row.id,
        org_id=key_row.org_id,
    )

    return PartnerAuthContext(
        key_id=key_row.id,
        org_id=key_row.org_id,
        zitadel_org_id=org.zitadel_org_id,
        permissions=key_row.permissions,
        kb_access=kb_access,
        rate_limit_rpm=key_row.rate_limit_rpm,
    )


def require_permission(auth: PartnerAuthContext, permission: str) -> None:
    """Raise 403 if the partner key does not have the specified permission.

    SPEC-API-001 REQ-2.3.
    """
    if not auth.permissions.get(permission, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"type": "permission_error", "message": "Insufficient permissions"}},
        )


def validate_kb_access(
    auth: PartnerAuthContext,
    requested_kb_ids: list[int] | None,
    required_level: str = "read",
) -> list[int]:
    """Validate and return KB IDs the partner key may access.

    SPEC-API-001 REQ-2.5:
    - Falls back to all key KBs if None requested
    - Raises 403 if any KB not in scope or level insufficient
    - Error message MUST be generic (never reveal KB existence)

    Args:
        auth: Resolved partner auth context.
        requested_kb_ids: Specific KB IDs to validate, or None for all.
        required_level: Minimum access level ('read' or 'read_write').

    Returns:
        List of validated KB IDs.
    """
    _LEVEL_RANK = {"read": 1, "read_write": 2}

    if requested_kb_ids is None:
        # Fall back to all KBs the key has access to with sufficient level
        required_rank = _LEVEL_RANK.get(required_level, 1)
        return [kb_id for kb_id, level in auth.kb_access.items() if _LEVEL_RANK.get(level, 0) >= required_rank]

    required_rank = _LEVEL_RANK.get(required_level, 1)
    for kb_id in requested_kb_ids:
        level = auth.kb_access.get(kb_id)
        if level is None or _LEVEL_RANK.get(level, 0) < required_rank:
            # Generic error — never reveal whether KB exists
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"type": "permission_error", "message": "Insufficient permissions"}},
            )

    return requested_kb_ids
