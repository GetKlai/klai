"""Async Redis connection pool singleton.

# @MX:NOTE: [AUTO] Lazy-init singleton pool for retrieval logs + feedback idempotency. SPEC-KB-015.

Usage:
    from app.services.redis_client import get_redis_pool
    pool = await get_redis_pool()
    if pool:
        await pool.set("key", "value", ex=3600)
"""

from urllib.parse import quote

import redis.asyncio as redis
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Mutable holder so tests can reset the singleton
_pool_holder: dict = {"pool": None}


def _safe_redis_url(url: str) -> str:
    """URL-encode the password in a redis:// URL to handle special characters.

    Passwords from `openssl rand -base64 32` contain `/`, `+`, `=` which break
    urlparse (the `/` is treated as a path separator, making the port field
    non-numeric). We extract the password with rfind("@") instead of urlparse
    so we never trigger the ValueError.

    Handles: redis://:PASSWORD@HOST:PORT
    """
    prefix = "redis://:"
    if not url.startswith(prefix):
        return url
    at_idx = url.rfind("@")
    if at_idx == -1:
        return url
    password = url[len(prefix) : at_idx]
    rest = url[at_idx + 1 :]  # host:port
    return f"redis://:{quote(password, safe='')}@{rest}"


async def get_redis_pool() -> redis.Redis | None:
    """Return the shared async Redis client, or None if redis_url is not configured."""
    if _pool_holder["pool"] is not None:
        return _pool_holder["pool"]

    if not settings.redis_url:
        return None

    _pool_holder["pool"] = redis.from_url(
        _safe_redis_url(settings.redis_url),
        decode_responses=True,
    )
    logger.info("redis_pool_initialized", url=settings.redis_url.split("@")[-1])
    return _pool_holder["pool"]
