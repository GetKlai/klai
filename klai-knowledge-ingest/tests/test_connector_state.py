"""Unit tests for connector_state utility.

SPEC-CONNECTOR-DELETE-LIFECYCLE-001 REQ-07.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import connector_state


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Each test starts with an empty cache."""
    connector_state.invalidate_cache()


def _mock_pool(state_value: str | None) -> MagicMock:
    pool = MagicMock()
    conn = MagicMock()
    if state_value is None:
        conn.fetchrow = AsyncMock(return_value=None)
    else:
        conn.fetchrow = AsyncMock(return_value={"state": state_value})
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    return pool


@pytest.mark.asyncio
async def test_connector_is_active_returns_true_when_state_active() -> None:
    pool = _mock_pool("active")
    with patch("knowledge_ingest.connector_state.get_pool", return_value=pool):
        assert await connector_state.connector_is_active("conn-uuid") is True


@pytest.mark.asyncio
async def test_connector_is_active_returns_false_when_state_deleting() -> None:
    pool = _mock_pool("deleting")
    with patch("knowledge_ingest.connector_state.get_pool", return_value=pool):
        assert await connector_state.connector_is_active("conn-uuid") is False


@pytest.mark.asyncio
async def test_connector_is_active_returns_false_when_row_missing() -> None:
    pool = _mock_pool(None)
    with patch("knowledge_ingest.connector_state.get_pool", return_value=pool):
        assert await connector_state.connector_is_active("conn-uuid") is False


@pytest.mark.asyncio
async def test_connector_is_active_returns_false_on_db_error() -> None:
    """Fail-closed: any DB error => abort enrichment."""
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=RuntimeError("connection refused"))
    with patch("knowledge_ingest.connector_state.get_pool", return_value=pool):
        assert await connector_state.connector_is_active("conn-uuid") is False


@pytest.mark.asyncio
async def test_connector_is_active_returns_false_on_empty_id() -> None:
    """No connector_id => not addressable => not active."""
    assert await connector_state.connector_is_active(None) is False
    assert await connector_state.connector_is_active("") is False


@pytest.mark.asyncio
async def test_state_is_cached_within_ttl() -> None:
    pool = _mock_pool("active")
    with patch("knowledge_ingest.connector_state.get_pool", return_value=pool):
        await connector_state.connector_is_active("conn-uuid")
        await connector_state.connector_is_active("conn-uuid")
        await connector_state.connector_is_active("conn-uuid")
    # acquire() should have been called only once thanks to the 5s cache.
    assert pool.acquire.call_count == 1


@pytest.mark.asyncio
async def test_state_lookup_failures_are_not_cached() -> None:
    """A transient DB hiccup must not poison the cache for 5 seconds."""
    pool = MagicMock()
    pool.acquire = MagicMock(side_effect=RuntimeError("blip"))
    with patch("knowledge_ingest.connector_state.get_pool", return_value=pool):
        await connector_state.connector_is_active("conn-uuid")
        await connector_state.connector_is_active("conn-uuid")
    # Both calls should have hit the pool — no caching of failures.
    assert pool.acquire.call_count == 2
