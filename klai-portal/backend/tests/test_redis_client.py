"""Verify Redis client singleton pool initialization.

SPEC-KB-015: portal-api needs async Redis client for retrieval logs and idempotency.
"""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_get_redis_pool_returns_client_when_configured():
    """When redis_url is set, get_redis_pool returns a Redis client."""
    mock_redis = AsyncMock()
    with (
        patch("app.services.redis_client.settings") as mock_settings,
        patch("app.services.redis_client.redis") as mock_redis_mod,
    ):
        mock_settings.redis_url = "redis://localhost:6379"
        mock_redis_mod.from_url.return_value = mock_redis

        from app.services.redis_client import get_redis_pool, _pool_holder

        _pool_holder["pool"] = None

        pool = await get_redis_pool()
        assert pool is mock_redis
        mock_redis_mod.from_url.assert_called_once_with(
            "redis://localhost:6379",
            decode_responses=True,
        )


@pytest.mark.asyncio
async def test_get_redis_pool_returns_none_when_unconfigured():
    """When redis_url is empty, get_redis_pool returns None."""
    with patch("app.services.redis_client.settings") as mock_settings:
        mock_settings.redis_url = ""

        from app.services.redis_client import get_redis_pool, _pool_holder

        _pool_holder["pool"] = None

        pool = await get_redis_pool()
        assert pool is None


@pytest.mark.asyncio
async def test_get_redis_pool_singleton():
    """Subsequent calls return the same pool instance."""
    mock_redis = AsyncMock()
    with (
        patch("app.services.redis_client.settings") as mock_settings,
        patch("app.services.redis_client.redis") as mock_redis_mod,
    ):
        mock_settings.redis_url = "redis://localhost:6379"
        mock_redis_mod.from_url.return_value = mock_redis

        from app.services.redis_client import get_redis_pool, _pool_holder

        _pool_holder["pool"] = None

        pool1 = await get_redis_pool()
        pool2 = await get_redis_pool()
        assert pool1 is pool2
        mock_redis_mod.from_url.assert_called_once()
