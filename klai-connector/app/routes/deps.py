"""Shared FastAPI dependencies for route handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, Literal

import structlog
from fastapi import Depends, HTTPException, Request

from app.core.config import Settings
from app.services.rate_limit import check_rate_limit

logger = structlog.get_logger(__name__)


def get_org_id(request: Request) -> str:
    """Extract org_id from the authenticated request state.

    Raises 401 if org_id is absent (request passed auth middleware without org_id).
    """
    org_id = getattr(request.state, "org_id", None)
    if org_id is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return str(org_id)


# ---------------------------------------------------------------------------
# Settings + Redis (SPEC-SEC-HYGIENE-001 HY-32)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached Settings singleton.

    Cached so each request handler doesn't re-parse the env. Tests
    override via ``app.dependency_overrides[get_settings]``.
    """
    return Settings()  # type: ignore[call-arg]


_redis_singleton: Any = None  # redis.asyncio.Redis at runtime; Any keeps
# the import lazy so dev environments without redis installed at import
# time do not crash on a feature they're not using.


async def get_redis_client(
    settings: Settings = Depends(get_settings),
) -> Any:
    """Lazy redis.asyncio singleton.

    Returns ``None`` when ``redis_url`` is empty — the rate-limit dep
    treats that as "feature disabled" (no-op). Tests override via
    ``app.dependency_overrides[get_redis_client]``.
    """
    global _redis_singleton
    if not settings.redis_url:
        return None
    if _redis_singleton is None:
        import redis.asyncio as aioredis

        _redis_singleton = aioredis.from_url(
            settings.redis_url, decode_responses=True
        )
    return _redis_singleton


# @MX:ANCHOR: enforce_org_rate_limit — fan_in = 5 (POST/GET-list/GET-by-id/PUT/DELETE
#   in routes/connectors.py, all via Depends()). Public API boundary for
#   per-org rate limiting; signature change ripples through every connector route.
# @MX:REASON: Fail-open contract (REQ-32.3) is invariant — any exception talking
#   to Redis MUST log connector_rate_limit_redis_unavailable at WARNING with
#   exc_info=True and allow the request through. Changing this to fail-closed
#   would convert a Redis outage into a tenant-wide CRUD outage.
# @MX:SPEC: SPEC-SEC-HYGIENE-001 REQ-32 (HY-32)
def enforce_org_rate_limit(
    method: Literal["read", "write"],
) -> Callable[..., Awaitable[None]]:
    """Build a per-route FastAPI dependency that enforces a per-org
    sliding-window rate limit. SPEC-SEC-HYGIENE-001 HY-32.

    Skips the check for portal control-plane calls
    (``request.state.from_portal`` is True — those use the
    ``portal_caller_secret`` bypass and are not user-quota traffic).
    Skips when org_id is None (auth middleware will already have
    rejected — defense in depth).

    Fail-open semantics (REQ-32.3): any exception while talking to
    Redis is logged at WARNING with ``exc_info`` and the request is
    allowed through.
    """

    async def _enforce(
        request: Request,
        settings: Settings = Depends(get_settings),
        redis_client: Any = Depends(get_redis_client),
    ) -> None:
        if getattr(request.state, "from_portal", False):
            return

        org_id = getattr(request.state, "org_id", None)
        if org_id is None:
            return

        limit = (
            settings.connector_rl_read_per_min
            if method == "read"
            else settings.connector_rl_write_per_min
        )

        key = f"connector_rl:{method}:{org_id}"
        try:
            allowed = await check_rate_limit(redis_client, key, limit)
        except Exception:
            logger.warning(
                "connector_rate_limit_redis_unavailable",
                org_id=str(org_id),
                method=method,
                exc_info=True,
            )
            return  # REQ-32.3: fail open
        if not allowed:
            raise HTTPException(
                status_code=429, detail="rate limit exceeded"
            )

    return _enforce
