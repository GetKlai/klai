"""Redis sliding-window rate limiter for SPEC-SEC-010 REQ-4.

Pattern mirrored from klai-portal/backend/app/services/partner_rate_limit.py:
    ZREMRANGEBYSCORE to drop entries older than the window
    ZCARD to count current entries
    ZADD to insert the new entry
    EXPIRE to auto-cleanup idle keys

Fail-open when Redis is unreachable (REQ-4.5) — availability outweighs strict
rate-limit enforcement when Redis itself is the failure mode. Every fail-open
decision is logged at WARN so it is auditable in VictoriaLogs.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = structlog.get_logger(__name__)

_REDIS_POOL: aioredis.Redis | None = None


async def get_redis_pool(redis_url: str) -> aioredis.Redis | None:
    """Return a singleton async Redis client, or None if import / connection fails.

    We intentionally do NOT raise here — callers must fail-open per REQ-4.5.
    """
    global _REDIS_POOL
    if _REDIS_POOL is not None:
        return _REDIS_POOL
    if not redis_url:
        return None
    try:
        import redis.asyncio as aioredis  # local import keeps redis optional at import time
    except ImportError:
        logger.warning("rate_limiter_degraded", reason="redis_import_failed")
        return None
    try:
        _REDIS_POOL = aioredis.from_url(redis_url, decode_responses=True)
    except Exception:
        logger.exception("rate_limiter_degraded", reason="redis_pool_init_failed")
        return None
    return _REDIS_POOL


async def check_and_increment(
    redis_url: str,
    key: str,
    limit_per_minute: int,
) -> tuple[bool, int]:
    """Check sliding window and increment counter.

    Returns
    -------
    (allowed, retry_after_seconds):
        allowed=True when under the limit (request should proceed).
        retry_after_seconds=0 when allowed, else seconds until the window opens.

    On Redis errors the function fails OPEN (returns allowed=True, retry_after=0)
    and logs ``rate_limiter_degraded`` at WARN per REQ-4.5.
    """
    pool = await get_redis_pool(redis_url)
    if pool is None:
        logger.warning("rate_limiter_degraded", reason="redis_unreachable", key=key)
        return True, 0

    now = time.time()
    window_start = now - 60

    try:
        async with pool.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            _, current_count = await pipe.execute()

        if current_count >= limit_per_minute:
            retry_after = max(1, math.ceil(60 - (now - window_start)))
            return False, retry_after

        member = f"{now}:{uuid.uuid4().hex[:8]}"
        async with pool.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {member: now})
            pipe.expire(key, 120)
            await pipe.execute()
        return True, 0
    # @MX:WARN: fail-open on Redis errors. The branch below returns
    #     ``(True, 0)`` (allow + no retry) for ANY redis exception,
    #     bypassing rate-limit enforcement when redis is the failure mode.
    # @MX:REASON: deliberate availability choice per SPEC-RETRIEVAL-RL-001
    #     REQ-4.5. retrieval-api sits on the hot path for user queries;
    #     fail-closed would take the service down whenever redis hiccups,
    #     which is worse than briefly serving a few unmetered requests.
    #     A future fail-closed-with-circuit-breaker variant is tracked as
    #     SPEC-RETRIEVAL-RL-FAILCLOSED-001. Annotated under
    #     SPEC-SEC-HYGIENE-001 REQ-42 so the next audit sees the rationale
    #     and does not re-file this as a finding.
    except Exception:
        logger.exception("rate_limiter_degraded", reason="redis_unreachable", key=key)
        return True, 0
