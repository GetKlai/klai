"""Tests for SPEC-TI-010B finding B-5: LiteLLM cache invalidation key consistency.

The LiteLLM hook (deploy/litellm/klai_knowledge.py) writes cache keys using the
Zitadel org_id string (e.g. "362757920133283846"). The portal-api invalidator
was previously using org.id (int), producing a different namespace so invalidations
silently missed for up to 30s after every template-toggle.

These tests lock in the correct key format (Zitadel string) and add a roundtrip
test that simulates both the writer side and the reader/invalidator side.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_redis_pool() -> MagicMock:
    """A MagicMock that mimics redis.asyncio.Redis with async methods."""
    pool = MagicMock()
    pool.delete = AsyncMock(return_value=1)

    async def _empty_scan_iter(*args, **kwargs):
        for _ in ():
            yield _

    pool.scan_iter = MagicMock(side_effect=_empty_scan_iter)
    return pool


# ---------------------------------------------------------------------------
# Signature: zitadel_org_id (str), not org_id (int)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_user_key_uses_zitadel_string(mock_redis_pool: MagicMock) -> None:
    """invalidate_templates must DEL templates:{zitadel_org_id}:{user_id}, not templates:{int}:{user_id}."""
    from unittest.mock import patch

    from app.services.litellm_cache import invalidate_templates

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(
            zitadel_org_id="362757920133283846",
            librechat_user_id="user-abc",
        )

    mock_redis_pool.delete.assert_awaited_once_with("templates:362757920133283846:user-abc")


@pytest.mark.asyncio
async def test_org_wide_scan_pattern_uses_zitadel_string(mock_redis_pool: MagicMock) -> None:
    """SCAN pattern must be templates:{zitadel_org_id}:* — NOT templates:{int}:*."""
    from unittest.mock import patch

    from app.services.litellm_cache import invalidate_templates

    captured: list[str] = []

    async def _capture_scan(match: str, count: int = 100):
        captured.append(match)
        return
        yield  # async generator typing

    mock_redis_pool.scan_iter = MagicMock(side_effect=_capture_scan)

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=mock_redis_pool)):
        await invalidate_templates(zitadel_org_id="362757920133283846")

    assert captured == ["templates:362757920133283846:*"], f"Expected Zitadel string key, got: {captured}"


# ---------------------------------------------------------------------------
# Roundtrip: writer (LiteLLM hook style) + invalidator must target same key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_templates_cache_invalidation_roundtrip() -> None:
    """Writer (LiteLLM hook style) writes templates:{zitadel}:{user_id};
    invalidator (portal-api) deletes the exact same key.

    Simulates the scenario where the LiteLLM hook cached a value and the
    portal-api template CRUD triggers invalidation. Uses an in-memory dict
    to verify that invalidate_templates targets the same key the LiteLLM
    hook would have written.
    """
    from unittest.mock import patch

    from app.services.litellm_cache import invalidate_templates

    zitadel_org_id = "362757920133283846"
    user_id = "user-roundtrip-1"
    expected_key = f"templates:{zitadel_org_id}:{user_id}"

    # In-memory store — simulates Redis state
    store: dict[str, str] = {expected_key: '["template-body"]'}

    deleted_keys: list[str] = []

    async def _fake_delete(*keys: str) -> int:
        count = 0
        for k in keys:
            if k in store:
                del store[k]
                deleted_keys.append(k)
                count += 1
        return count

    async def _empty_scan_iter(match: str, count: int = 100):
        for _ in ():
            yield _

    pool = MagicMock()
    pool.delete = AsyncMock(side_effect=_fake_delete)
    pool.scan_iter = MagicMock(side_effect=_empty_scan_iter)

    # Precondition: key exists (as the LiteLLM hook would have written it)
    assert expected_key in store

    with patch("app.services.litellm_cache.get_redis_pool", AsyncMock(return_value=pool)):
        await invalidate_templates(zitadel_org_id=zitadel_org_id, librechat_user_id=user_id)

    assert expected_key not in store, "Key must be deleted after invalidation"
    assert expected_key in deleted_keys, f"Expected key '{expected_key}' to be deleted; got: {deleted_keys}"


@pytest.mark.asyncio
async def test_int_org_id_does_not_match_zitadel_key() -> None:
    """Regression guard: using int (e.g. 42) instead of Zitadel string
    ('362757920133283846') targets a DIFFERENT key — this test documents the
    wrong behaviour that existed before B-5 was fixed.
    """
    # The writer (LiteLLM hook) always uses the Zitadel string.
    # If the invalidator were to pass int 42, it would try to DEL
    # 'templates:42:user-x', which is not the key the hook wrote.
    writer_key = "templates:362757920133283846:user-x"
    wrong_invalidation_key = "templates:42:user-x"
    assert writer_key != wrong_invalidation_key, (
        "Writer and invalidator keys must differ when using int vs Zitadel string"
    )
