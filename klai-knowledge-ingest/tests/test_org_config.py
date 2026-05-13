"""Tests for knowledge_ingest/org_config.py.

SPEC-TI-003-FOLLOWUP-001: ``is_enrichment_enabled`` now takes
asyncpg.Connection (not Pool).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import knowledge_ingest.org_config as oc


def _make_conn(row=None):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    return conn


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the TTL cache before each test."""
    oc._cache.clear()
    yield
    oc._cache.clear()


@pytest.mark.asyncio
async def test_global_kill_switch_overrides_db():
    conn = _make_conn()
    with patch.object(oc.settings, "enrichment_enabled", False):
        result = await oc.is_enrichment_enabled(conn, "org-123")
    assert result is False
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_default_enabled_when_no_db_row():
    conn = _make_conn(row=None)
    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled(conn, "org-new")
    assert result is True


@pytest.mark.asyncio
async def test_db_row_false_disables_org():
    row = {"enrichment_enabled": False}
    conn = _make_conn(row=row)
    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled(conn, "org-disabled")
    assert result is False


@pytest.mark.asyncio
async def test_db_row_null_defaults_to_enabled():
    row = {"enrichment_enabled": None}
    conn = _make_conn(row=row)
    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled(conn, "org-null")
    assert result is True


@pytest.mark.asyncio
async def test_cache_hit_skips_db():
    conn = _make_conn()
    oc._cache["org-cached"] = True

    with patch.object(oc.settings, "enrichment_enabled", True):
        result = await oc.is_enrichment_enabled(conn, "org-cached")

    assert result is True
    conn.fetchrow.assert_not_called()


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
    conn = _make_conn(row=row)

    with patch.object(oc.settings, "enrichment_enabled", True):
        await oc.is_enrichment_enabled(conn, "org-store")
        # Second call should use cache
        await oc.is_enrichment_enabled(conn, "org-store")

    assert conn.fetchrow.call_count == 1
