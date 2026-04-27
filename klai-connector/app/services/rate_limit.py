"""Per-org sliding-window rate limit using a Redis sorted set.

Mirrors klai-portal/backend/app/services/partner_rate_limit.py
(SPEC-API-001 REQ-2.4) — same ZSET + ZREMRANGEBYSCORE/ZCARD/ZADD/EXPIRE
algorithm, scoped per-org instead of per-API-key.

SPEC-SEC-HYGIENE-001 HY-32 (REQ-32.1b/32.2/32.3/32.4):
- Window: 60 seconds.
- Keys: ``connector_rl:{method}:{org_id}`` where method is "read" or "write".
- Caller sets ``limit_per_minute`` per method.
- ``redis_pool=None`` (empty REDIS_URL) → no-op (returns True). The feature
  is opt-in via env, so dev environments without a Redis sidecar are
  unaffected.
- Exceptions propagate to the caller, which is expected to log and fail
  open per REQ-32.3.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis  # pragma: no cover — typing only


_WINDOW_SECONDS = 60.0


def _now() -> float:
    """Indirection so tests can monkey-patch the clock without touching
    every module that reads time.time().
    """
    return time.time()


async def check_rate_limit(
    redis_pool: aioredis.Redis | None,
    redis_key: str,
    limit_per_minute: int,
) -> bool:
    """Check + increment a sliding-window counter.

    Returns True if the request is within the limit (and is recorded),
    False if it exceeds the limit (and is NOT recorded). Raises whatever
    the redis client raises — the caller is responsible for catching and
    failing open per REQ-32.3.
    """
    if redis_pool is None or limit_per_minute <= 0:
        return True

    now = _now()
    window_start = now - _WINDOW_SECONDS

    # Drop expired entries first so the count below is exact.
    await redis_pool.zremrangebyscore(redis_key, 0, window_start)

    current = await redis_pool.zcard(redis_key)
    if current >= limit_per_minute:
        return False

    # Add a unique entry — uuid suffix avoids ZADD collisions when two
    # requests land at the same float timestamp (Windows clock granularity).
    member = f"{now}:{uuid.uuid4().hex[:8]}"
    await redis_pool.zadd(redis_key, {member: now})
    # Belt-and-braces TTL so a never-revisited key eventually clears.
    await redis_pool.expire(redis_key, int(math.ceil(_WINDOW_SECONDS * 2)))
    return True
