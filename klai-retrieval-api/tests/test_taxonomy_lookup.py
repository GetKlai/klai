"""SPEC-RAG-TAXONOMY-001 — taxonomy_lookup.py unit tests.

Tests tree fetch, coverage fetch, in-process cache, and fail-open behaviour.
All tests mock the asyncpg pool so no real DB is needed.
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset in-process TTL caches before each test to prevent cross-test pollution."""
    from retrieval_api.services import taxonomy_lookup

    taxonomy_lookup._tree_cache.clear()
    taxonomy_lookup._coverage_cache.clear()
    yield
    taxonomy_lookup._tree_cache.clear()
    taxonomy_lookup._coverage_cache.clear()


def _make_pool_mock(
    fetch_rows=None, fetchrow_value=None, raise_on_fetch=False, raise_on_fetchrow=False
):
    """Build a minimal asyncpg Pool mock.

    ``fetch_rows`` is the list of row-like dicts returned by pool.fetch().
    ``fetchrow_value`` is the dict returned by pool.fetchrow().
    """

    class _FakeRow(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)

    class _FakePool:
        async def fetch(self, sql, *args):
            if raise_on_fetch:
                raise RuntimeError("DB exploded")
            return [_FakeRow(r) for r in (fetch_rows or [])]

        async def fetchrow(self, sql, *args):
            if raise_on_fetchrow:
                raise RuntimeError("DB exploded")
            if fetchrow_value is None:
                return None
            return _FakeRow(fetchrow_value)

    return _FakePool()


# ---------------------------------------------------------------------------
# get_taxonomy_tree
# ---------------------------------------------------------------------------


class TestGetTaxonomyTree:
    @pytest.mark.asyncio
    async def test_returns_tree_from_db(self, monkeypatch):
        """Happy path: pool.fetch returns rows → list of TaxonomyNode dicts."""
        rows = [
            {"id": 1, "name": "Root", "parent_id": None, "depth": 0},
            {"id": 2, "name": "Child", "parent_id": 1, "depth": 1},
        ]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_tree

        result = await get_taxonomy_tree("org-1", "my-kb")

        assert len(result) == 2
        assert result[0]["id"] == 1
        assert result[0]["name"] == "Root"
        assert result[0]["parent_id"] is None
        assert result[0]["depth"] == 0
        assert result[1]["id"] == 2
        assert result[1]["parent_id"] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_when_pool_is_none(self, monkeypatch):
        """No DB pool → fail-open, return []."""
        monkeypatch.setattr("retrieval_api.services.events._pool", None)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_tree

        result = await get_taxonomy_tree("org-1", "my-kb")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_db_error(self, monkeypatch):
        """DB exception → fail-open, return []."""
        pool = _make_pool_mock(raise_on_fetch=True)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_tree

        result = await get_taxonomy_tree("org-1", "my-kb")
        assert result == []

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self, monkeypatch):
        """Second call with same (org_id, kb_slug) returns cached result without hitting DB."""
        rows = [{"id": 42, "name": "Cached", "parent_id": None, "depth": 0}]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_tree

        # First call — populates cache
        first = await get_taxonomy_tree("org-c", "kb-c")
        assert len(first) == 1

        # Swap pool to one that raises — should NOT be called
        bad_pool = _make_pool_mock(raise_on_fetch=True)
        monkeypatch.setattr("retrieval_api.services.events._pool", bad_pool)

        # Second call — must come from cache
        second = await get_taxonomy_tree("org-c", "kb-c")
        assert second == first

    @pytest.mark.asyncio
    async def test_expired_cache_refetches(self, monkeypatch):
        """Cache entry past TTL triggers a fresh fetch."""
        rows = [{"id": 7, "name": "Fresh", "parent_id": None, "depth": 0}]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services import taxonomy_lookup
        from retrieval_api.services.taxonomy_lookup import get_taxonomy_tree

        # Pre-seed cache with an already-expired entry
        taxonomy_lookup._tree_cache[("org-e", "kb-e")] = (
            time.monotonic() - 1.0,  # already expired
            [{"id": 99, "name": "Stale", "parent_id": None, "depth": 0}],
        )

        result = await get_taxonomy_tree("org-e", "kb-e")

        # Should have fetched fresh from DB
        assert result[0]["id"] == 7
        assert result[0]["name"] == "Fresh"

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_kb_with_no_nodes(self, monkeypatch):
        """DB returns zero rows → empty list (no tree)."""
        pool = _make_pool_mock(fetch_rows=[])
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_tree

        result = await get_taxonomy_tree("org-1", "empty-kb")
        assert result == []


# ---------------------------------------------------------------------------
# get_kb_taxonomy_coverage
# ---------------------------------------------------------------------------


class TestGetKbTaxonomyCoverage:
    @pytest.mark.asyncio
    async def test_returns_coverage_from_db(self, monkeypatch):
        """Happy path: pool.fetchrow returns a coverage_ratio → float returned."""
        pool = _make_pool_mock(fetchrow_value={"coverage_ratio": 0.72})
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", "my-kb")
        assert abs(result - 0.72) < 1e-6

    @pytest.mark.asyncio
    async def test_returns_zero_when_pool_is_none(self, monkeypatch):
        """No pool → 0.0 (fail-open)."""
        monkeypatch.setattr("retrieval_api.services.events._pool", None)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", "my-kb")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_on_db_error(self, monkeypatch):
        """DB exception → 0.0 (fail-open)."""
        pool = _make_pool_mock(raise_on_fetchrow=True)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", "my-kb")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_coverage_ratio_is_null(self, monkeypatch):
        """DB returns coverage_ratio=NULL (empty KB) → 0.0."""
        pool = _make_pool_mock(fetchrow_value={"coverage_ratio": None})
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", "empty-kb")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_when_fetchrow_returns_none(self, monkeypatch):
        """DB returns no row at all → 0.0."""
        pool = _make_pool_mock(fetchrow_value=None)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", "no-kb")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self, monkeypatch):
        """Second call returns cached coverage without hitting DB again."""
        pool = _make_pool_mock(fetchrow_value={"coverage_ratio": 0.55})
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        first = await get_kb_taxonomy_coverage("org-cc", "kb-cc")
        assert abs(first - 0.55) < 1e-6

        # Replace pool with one that raises — must not be reached
        bad_pool = _make_pool_mock(raise_on_fetchrow=True)
        monkeypatch.setattr("retrieval_api.services.events._pool", bad_pool)

        second = await get_kb_taxonomy_coverage("org-cc", "kb-cc")
        assert abs(second - 0.55) < 1e-6

    @pytest.mark.asyncio
    async def test_different_kb_slugs_are_cached_independently(self, monkeypatch):
        """Each (org_id, kb_slug) pair has its own cache entry."""
        call_count = 0

        class _CountingPool:
            async def fetchrow(self, sql, org_id, kb_slug):
                nonlocal call_count
                call_count += 1
                return {"coverage_ratio": 0.3 if kb_slug == "kb-a" else 0.8}

        monkeypatch.setattr("retrieval_api.services.events._pool", _CountingPool())

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        cov_a = await get_kb_taxonomy_coverage("org-1", "kb-a")
        cov_b = await get_kb_taxonomy_coverage("org-1", "kb-b")

        assert abs(cov_a - 0.3) < 1e-6
        assert abs(cov_b - 0.8) < 1e-6
        assert call_count == 2  # one fetch each

        # Cached now — no more DB calls
        await get_kb_taxonomy_coverage("org-1", "kb-a")
        await get_kb_taxonomy_coverage("org-1", "kb-b")
        assert call_count == 2
