"""Partner API authentication and authorization dependencies.

SPEC-API-001 REQ-2.1 through REQ-2.6:
- Extract Bearer pk_... token from Authorization header
- SHA-256 hash lookup in partner_api_keys (active=True only)
- Rate limit enforcement via Redis sliding window
- Non-blocking last_used_at update
- Error messages never distinguish not-found from inactive (no enumeration)
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.models.portal import PortalOrg
from app.services.partner_keys import verify_partner_key
from app.services.partner_rate_limit import check_rate_limit
from app.services.redis_client import get_redis_pool

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


async def _update_last_used(key_id: str, db: AsyncSession) -> None:
    """Update last_used_at timestamp (fire-and-forget)."""
    try:
        await db.execute(update(PartnerAPIKey).where(PartnerAPIKey.id == key_id).values(last_used_at=datetime.now(UTC)))
        await db.commit()
    except Exception:
        logger.exception("Failed to update last_used_at", partner_key_id=key_id)


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

    # Step 2: Validate prefix
    if not token.startswith("pk_live_"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

    # Step 3: Compute hash and look up key (active only — inactive returns None)
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.key_hash == key_hash,
            PartnerAPIKey.active.is_(True),
        )
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

    # Step 6: Resolve org_id -> zitadel_org_id
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == key_row.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_AUTH_ERROR)

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
    task = asyncio.create_task(_update_last_used(key_row.id, db))
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
