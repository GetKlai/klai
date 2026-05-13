"""User-facing MCP token endpoints (SPEC-MCP-AUTH-001 REQ-29 / Fase 4).

Two endpoints under ``/api/me/mcp-tokens``:

- ``GET    /api/me/mcp-tokens``        — list tokens for the caller (no hashes)
- ``DELETE /api/me/mcp-tokens/{id}``   — revoke a single token

There is intentionally NO ``POST /api/me/mcp-tokens`` (token-create endpoint).
Per SPEC-MCP-AUTH-001 v0.2.1 token issuance is OAuth-only — the user can
not mint a personal access token by clicking a button. If we add that
later it lives in a separate SPEC.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.models.mcp_oauth import PortalOAuthClient
from app.models.portal import PortalUser
from app.services import audit
from app.services import mcp_oauth as svc
from app.services.redis_client import get_redis_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me/mcp-tokens", tags=["me", "mcp-oauth"])


class _ConnectedAppResponse(BaseModel):
    """One row in the Connected Applications list view.

    Hashes are NEVER returned. Only metadata that helps the user identify
    which client this is and when it was last used.
    """

    id: int
    client_name: str
    application_type: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    refresh_expires_at: datetime | None
    revoked_at: datetime | None


@router.get("", response_model=list[_ConnectedAppResponse])
async def list_my_tokens(
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> list[_ConnectedAppResponse]:
    """Return the caller's tokens, newest first."""
    user_result = await db.execute(
        select(PortalUser).where(PortalUser.zitadel_user_id == perms.user_id, PortalUser.org_id == perms.org_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    rows = await svc.list_user_tokens(db, org_id=perms.org_id, user_id=user.id)
    if not rows:
        return []

    # Bulk-fetch client_name for each unique client_id.
    client_db_ids = {row.client_id for row in rows}
    clients: dict[int, PortalOAuthClient] = {}
    if client_db_ids:
        result = await db.execute(select(PortalOAuthClient).where(PortalOAuthClient.id.in_(client_db_ids)))
        for client in result.scalars():
            clients[client.id] = client

    out: list[_ConnectedAppResponse] = []
    for row in rows:
        client = clients.get(row.client_id)
        out.append(
            _ConnectedAppResponse(
                id=row.id,
                client_name=client.client_name if client else "(unknown client)",
                application_type=client.application_type if client else "unknown",
                scopes=list(row.scopes or [svc.DEFAULT_SCOPE]),
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                refresh_expires_at=row.refresh_expires_at,
                revoked_at=row.revoked_at,
            )
        )
    return out


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_token(
    token_id: int,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke a token. Idempotent — already-revoked returns 204."""
    user_result = await db.execute(
        select(PortalUser).where(PortalUser.zitadel_user_id == perms.user_id, PortalUser.org_id == perms.org_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user_not_found")

    redis = await get_redis_pool()
    if redis is None:
        # Cache invalidation is best-effort, but we still want the DB write
        # to succeed. Continue with a no-op redis-stub when unavailable.
        # NOTE: callers will see up-to-60s cache stickiness in this case.
        class _NullRedis:
            async def delete(self, *_args, **_kw):
                return 0

            async def set(self, *_args, **_kw):
                return None

        redis = _NullRedis()

    ok = await svc.revoke_token(db, redis, token_id=token_id, org_id=perms.org_id, user_id=user.id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token_not_found")
    await db.commit()

    try:
        await audit.log_event(
            org_id=perms.org_id,
            actor=perms.user_id,
            action="mcp_token.revoked",
            resource_type="mcp_token",
            resource_id=str(token_id),
        )
    except Exception:  # pragma: no cover
        logger.warning("mcp_token_revoked_audit_failed", exc_info=True)
