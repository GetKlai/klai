"""Tests for knowledge_ingest/org_config.py"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import knowledge_ingest.org_config as oc


def _make_pool(row=None):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the TTL cache before each test."""
    oc._cache.clear()
    yield
    oc._cache.clear()


@pytest.mark.asyncio
async def test_global_kill_switch_overrides_db():
    pool = _make_pool()
    with patch.object(oc.settings, "enrichment_enabled", False):
        result = await oc.is_enrichment_enabled("org-123", pool)
    assert result is False
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_default_enabled_when_no_db_row():
    pool = _make_pool(row=None)
    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled("org-new", pool)
    assert result is True


@pytest.mark.asyncio
async def test_db_row_false_disables_org():
    row = {"enrichment_enabled": False}
    pool = _make_pool(row=row)
    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled("org-disabled", pool)
    assert result is False


@pytest.mark.asyncio
async def test_db_row_null_defaults_to_enabled():
    row = {"enrichment_enabled": None}
    pool = _make_pool(row=row)
    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled("org-null", pool)
    assert result is True


@pytest.mark.asyncio
async def test_cache_hit_skips_db():
    pool = _make_pool()
    oc._cache["org-cached"] = True

    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled("org-cached", pool)

    assert result is True
    pool.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_cache_eviction_on_notify():
    oc._cache["org-evict"] = True
    oc._on_org_config_changed(MagicMock(), 0, "org_config_changed", "org-evict")
    assert "org-evict" not in oc._cache


@pytest.mark.asyncio
async def test_cache_eviction_unknown_org_is_noop():
    # Evicting an org that is not in cache should not raise
    oc._on_org_config_changed(MagicMock(), 0, "org_config_changed", "org-unknown")


@pytest.mark.asyncio
async def test_result_cached_after_db_query():
    row = {"enrichment_enabled": True}
    pool = _make_pool(row=row)

    with patch.object(oc.settings, "enrichment_enabled", True):
        await oc.is_enrichment_enabled("org-store", pool)
        # Second call should use cache
        await oc.is_enrichment_enabled("org-store", pool)

    assert pool.fetchrow.call_count == 1
