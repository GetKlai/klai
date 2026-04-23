"""Tests for app.services.litellm_cache.invalidate_templates.

Locks in SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CACHE: single DEL for
user-specific changes, SCAN+DEL for org-wide changes, fire-and-forget
semantics on Redis errors.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_redis_pool() -> MagicMock:
    """A MagicMock that mimics redis.asyncio.Redis with async methods."""
    pool = MagicMock()
    pool.delete = AsyncMock(return_value=1)

    # scan_iter is an async iterator, not a coroutine.
    async def _empty_scan_iter(*args, **kwargs):
        for _ in ():
            yield _

    pool.scan_iter = MagicMock(side_effect=_empty_scan_iter)
    return pool


@pytest.mark.asyncio
async def test_single_user_invalidation_calls_delete_exactly_once(mock_redis_pool: MagicMock) -> None:
    """Scope="personal" or active_template_ids change → single DEL on exact key."""
    from app.services.litellm_cache import invalidate_templates

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(org_id=42, librechat_user_id="507f1f77bcf86cd799439011")

    mock_redis_pool.delete.assert_awaited_once_with("templates:42:507f1f77bcf86cd799439011")
    mock_redis_pool.scan_iter.assert_not_called()


@pytest.mark.asyncio
async def test_org_wide_invalidation_scans_and_deletes(mock_redis_pool: MagicMock) -> None:
    """Scope="org" write → SCAN pattern + DEL per matching key."""
    from app.services.litellm_cache import invalidate_templates

    async def _scan_with_keys(match: str, count: int = 100):
        # Simulate 3 matching keys for this org.
        yield "templates:42:user-a"
        yield "templates:42:user-b"
        yield "templates:42:user-c"

    mock_redis_pool.scan_iter = MagicMock(side_effect=_scan_with_keys)

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(org_id=42)

    assert mock_redis_pool.delete.await_count == 3
    mock_redis_pool.delete.assert_any_await("templates:42:user-a")
    mock_redis_pool.delete.assert_any_await("templates:42:user-b")
    mock_redis_pool.delete.assert_any_await("templates:42:user-c")


@pytest.mark.asyncio
async def test_org_wide_invalidation_passes_correct_pattern(mock_redis_pool: MagicMock) -> None:
    """SCAN match pattern MUST be templates:{org_id}:* (star wildcard)."""
    from app.services.litellm_cache import invalidate_templates

    captured_match: list[str] = []

    async def _capture_scan(match: str, count: int = 100):
        captured_match.append(match)
        return
        yield  # unreachable; keeps mypy happy for async generator typing

    mock_redis_pool.scan_iter = MagicMock(side_effect=_capture_scan)

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(org_id=7)

    assert captured_match == ["templates:7:*"]


@pytest.mark.asyncio
async def test_redis_pool_none_is_no_op() -> None:
    """If get_redis_pool returns None (Redis not configured), do nothing."""
    from app.services.litellm_cache import invalidate_templates

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=None)):
        # Should not raise.
        await invalidate_templates(org_id=1, librechat_user_id="x")
        await invalidate_templates(org_id=1)


@pytest.mark.asyncio
async def test_redis_pool_raise_is_swallowed() -> None:
    """get_redis_pool raising → warning logged, function returns cleanly."""
    from app.services.litellm_cache import invalidate_templates

    with patch(
        "app.services.litellm_cache.get_redis_pool",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        # Must not raise.
        await invalidate_templates(org_id=1, librechat_user_id="x")


@pytest.mark.asyncio
async def test_delete_raise_is_swallowed_single(mock_redis_pool: MagicMock) -> None:
    """Single-key DEL raising → warning logged, function returns cleanly."""
    from app.services.litellm_cache import invalidate_templates

    mock_redis_pool.delete = AsyncMock(side_effect=RuntimeError("connection reset"))

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(org_id=1, librechat_user_id="x")


@pytest.mark.asyncio
async def test_scan_raise_is_swallowed_org_wide(mock_redis_pool: MagicMock) -> None:
    """Org-wide SCAN raising → warning logged, function returns cleanly."""
    from app.services.litellm_cache import invalidate_templates

    def _boom(match: str, count: int = 100):
        raise RuntimeError("scan error")

    mock_redis_pool.scan_iter = MagicMock(side_effect=_boom)

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(org_id=1)
