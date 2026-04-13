"""RED: Verify Redis sliding-window rate limiter.

SPEC-API-001 REQ-2.4:
- Under limit: allowed
- At limit: denied with retry_after > 0
- Expired entries cleaned up
"""

import time
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_redis():
    """Mock Redis with ZSET behavior using an in-memory dict."""
    store: dict[str, list[tuple[float, str]]] = {}

    redis = AsyncMock()

    async def zremrangebyscore(key, min_score, max_score):
        if key in store:
            store[key] = [(s, m) for s, m in store[key] if not (min_score <= s <= max_score)]
        return 0

    async def zcard(key):
        return len(store.get(key, []))

    async def zadd(key, mapping):
        if key not in store:
            store[key] = []
        for member, score in mapping.items():
            store[key].append((score, member))
        return 1

    async def expire(key, seconds):
        return True

    redis.zremrangebyscore = AsyncMock(side_effect=zremrangebyscore)
    redis.zcard = AsyncMock(side_effect=zcard)
    redis.zadd = AsyncMock(side_effect=zadd)
    redis.expire = AsyncMock(side_effect=expire)
    redis._store = store  # expose for test manipulation

    return redis


@pytest.mark.asyncio
async def test_under_limit_allowed(mock_redis):
    """Request under rate limit is allowed."""
    from app.services.partner_rate_limit import check_rate_limit

    allowed, retry_after = await check_rate_limit(mock_redis, "key-1", limit_per_minute=60)
    assert allowed is True
    assert retry_after == 0


@pytest.mark.asyncio
async def test_at_limit_denied(mock_redis):
    """Request at rate limit is denied with retry_after > 0."""
    from app.services.partner_rate_limit import check_rate_limit

    # Fill up to the limit
    now = time.time()
    key = "partner_rl:key-2"
    mock_redis._store[key] = [(now - i * 0.1, f"req-{i}") for i in range(5)]

    allowed, retry_after = await check_rate_limit(mock_redis, "key-2", limit_per_minute=5)
    assert allowed is False
    assert retry_after > 0


@pytest.mark.asyncio
async def test_expired_entries_cleaned(mock_redis):
    """Expired entries (older than 60s) are cleaned before counting."""
    from app.services.partner_rate_limit import check_rate_limit

    # Add entries that are older than 60 seconds
    key = "partner_rl:key-3"
    old_time = time.time() - 120  # 2 minutes ago
    mock_redis._store[key] = [(old_time + i, f"old-req-{i}") for i in range(10)]

    # zremrangebyscore will clean old entries (our mock removes based on score range)
    allowed, retry_after = await check_rate_limit(mock_redis, "key-3", limit_per_minute=5)
    # After cleaning old entries, should be under limit
    assert allowed is True
    assert retry_after == 0


@pytest.mark.asyncio
async def test_retry_after_is_positive_seconds(mock_redis):
    """retry_after is a positive integer (seconds until window opens)."""
    from app.services.partner_rate_limit import check_rate_limit

    now = time.time()
    key = "partner_rl:key-4"
    # 3 requests in the last 30 seconds
    mock_redis._store[key] = [(now - 30 + i, f"req-{i}") for i in range(3)]

    allowed, retry_after = await check_rate_limit(mock_redis, "key-4", limit_per_minute=3)
    assert allowed is False
    assert 0 < retry_after <= 60
