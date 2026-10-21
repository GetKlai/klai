"""Tests for app.services.litellm_cache.invalidate_kb_cache + key-shape drift.

Regression for the int-vs-Zitadel-string cache-key mismatch (HIGH-1): the
LiteLLM hook keys ``kb_ver:{zitadel_org_id}:{user}`` on the Zitadel org-id
STRING, but the portal-side invalidation used the integer ``org_id`` — so the
DELETE never matched and "Open" in the UI could keep running "Strict" in the
backend until the 30s TTL lapsed.

These tests lock:
1. invalidate_kb_cache single/org-wide DEL behaviour + fire-and-forget.
2. The key SHAPES the portal builds.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.litellm_cache import (
    _kb_version_org_pattern,
    _kb_version_user_key,
    invalidate_kb_cache,
)


@pytest.fixture
def mock_redis_pool() -> MagicMock:
    pool = MagicMock()
    pool.delete = AsyncMock(return_value=1)

    async def _empty_scan_iter(*args, **kwargs):
        for _ in ():
            yield _

    pool.scan_iter = MagicMock(side_effect=_empty_scan_iter)
    return pool


# -- Key-shape contract -------------------------------------------------------


def test_kb_version_user_key_shape() -> None:
    assert _kb_version_user_key("300000000000000002", "abc123") == "kb_ver:300000000000000002:abc123"


def test_kb_version_org_pattern_shape() -> None:
    assert _kb_version_org_pattern("300000000000000002") == "kb_ver:300000000000000002:*"


# -- Behaviour ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_user_invalidation_deletes_exact_zitadel_key(mock_redis_pool: MagicMock) -> None:
    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_kb_cache("300000000000000002", "507f1f77bcf86cd799439011")

    mock_redis_pool.delete.assert_awaited_once_with("kb_ver:300000000000000002:507f1f77bcf86cd799439011")
    mock_redis_pool.scan_iter.assert_not_called()


@pytest.mark.asyncio
async def test_org_wide_invalidation_scans_and_deletes(mock_redis_pool: MagicMock) -> None:
    async def _scan_with_keys(match: str, count: int = 100):
        yield "kb_ver:300000000000000002:user-a"
        yield "kb_ver:300000000000000002:user-b"

    mock_redis_pool.scan_iter = MagicMock(side_effect=_scan_with_keys)

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_kb_cache("300000000000000002")

    assert mock_redis_pool.delete.await_count == 2
    mock_redis_pool.delete.assert_any_await("kb_ver:300000000000000002:user-a")
    mock_redis_pool.delete.assert_any_await("kb_ver:300000000000000002:user-b")


@pytest.mark.asyncio
async def test_org_wide_invalidation_uses_star_pattern(mock_redis_pool: MagicMock) -> None:
    captured: list[str] = []

    async def _capture_scan(match: str, count: int = 100):
        captured.append(match)
        return
        yield  # async-generator typing

    mock_redis_pool.scan_iter = MagicMock(side_effect=_capture_scan)

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_kb_cache("zit-7")

    assert captured == ["kb_ver:zit-7:*"]


@pytest.mark.asyncio
async def test_redis_pool_none_is_no_op() -> None:
    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=None)):
        await invalidate_kb_cache("z", "x")
        await invalidate_kb_cache("z")


@pytest.mark.asyncio
async def test_redis_errors_are_swallowed(mock_redis_pool: MagicMock) -> None:
    with patch(
        "app.services.litellm_cache.get_redis_pool",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        await invalidate_kb_cache("z", "x")  # must not raise

    mock_redis_pool.delete = AsyncMock(side_effect=RuntimeError("conn reset"))
    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_kb_cache("z", "x")  # must not raise
