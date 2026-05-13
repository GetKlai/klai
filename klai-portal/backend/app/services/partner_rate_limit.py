"""Redis sliding-window rate limiter for partner API keys.

SPEC-API-001 REQ-2.4:
- ZSET-based sliding window: 1-minute window per key
- ZREMRANGEBYSCORE to remove expired entries
- ZCARD to count current requests
- ZADD to add new entry with timestamp score
"""

from __future__ import annotations

import math
import time
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis


async def check_rate_limit(
    redis_pool: aioredis.Redis,
    key_id: str,
    limit_per_minute: int,
    window_seconds: int = 60,
) -> tuple[bool, int]:
    """Check and increment sliding-window rate limit.

    Args:
        redis_pool: Async Redis connection.
        key_id: Partner API key UUID string.
        limit_per_minute: Maximum requests per sliding window.
        window_seconds: Sliding window size. Defaults to 60 seconds for the
            existing partner/internal API callers.

    Returns:
        (allowed, retry_after_seconds): allowed=True if under limit,
        retry_after_seconds=0 if allowed, else seconds until window opens.
    """
    now = time.time()
    window_start = now - window_seconds
    redis_key = f"partner_rl:{key_id}"

    # Remove entries older than the window
    await redis_pool.zremrangebyscore(redis_key, 0, window_start)

    # Count current entries in window
    current_count = await redis_pool.zcard(redis_key)

    if current_count >= limit_per_minute:
        # Calculate retry_after: time until the oldest entry expires
        # The oldest entry in the window will expire at oldest_score + window_seconds
        # We return ceil of the difference
        oldest = await redis_pool.zrange(redis_key, 0, 0, withscores=True)
        oldest_score = oldest[0][1] if oldest else window_start
        retry_after = max(1, math.ceil(window_seconds - (now - float(oldest_score))))
        return False, retry_after

    # Add new entry
    member = f"{now}:{uuid.uuid4().hex[:8]}"
    await redis_pool.zadd(redis_key, {member: now})

    # Set TTL on the key to auto-cleanup
    await redis_pool.expire(redis_key, window_seconds * 2)

    return True, 0
