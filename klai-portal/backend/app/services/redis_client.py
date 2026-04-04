"""Async Redis connection pool singleton.

# @MX:NOTE: [AUTO] Lazy-init singleton pool for retrieval logs + feedback idempotency. SPEC-KB-015.

Usage:
    from app.services.redis_client import get_redis_pool
    pool = await get_redis_pool()
    if pool:
        await pool.set("key", "value", ex=3600)
"""

import redis.asyncio as redis
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Mutable holder so tests can reset the singleton
_pool_holder: dict = {"pool": None}


async def get_redis_pool() -> redis.Redis | None:
    """Return the shared async Redis client, or None if redis_url is not configured."""
    if _pool_holder["pool"] is not None:
        return _pool_holder["pool"]

    if not settings.redis_url:
        return None

    _pool_holder["pool"] = redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    logger.info("redis_pool_initialized", url=settings.redis_url.split("@")[-1])
    return _pool_holder["pool"]
