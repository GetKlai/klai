"""SPEC-RAG-TAXONOMY-001 (multi-KB) — taxonomy_lookup.py unit tests.

Tests the v2 multi-KB API:
- ``get_taxonomy_trees(org_id, kb_slugs)`` returns ``{kb_slug: [node, ...]}``.
- ``get_kb_taxonomy_coverage(org_id, kb_slugs)`` returns
  ``{kb_slug: 0.0|1.0}`` — binary signal (KB has nodes vs not).
- Both functions fail-open: any DB error returns empty / zero so the
  hook proceeds without taxonomy narrowing.

Caching is intentionally absent at this layer — the LiteLLM hook owns
the Redis cache, this module only owns the DB lookup. All tests mock
the asyncpg pool so no real DB is needed.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Pool mock helpers
# ---------------------------------------------------------------------------


def _make_pool_mock(fetch_rows=None, raise_on_fetch=False, capture_args=None):
    """Build a minimal asyncpg Pool mock supporting ``fetch``.

    ``fetch_rows`` is the list of row-like dicts returned by pool.fetch().
    ``capture_args`` (optional list) collects (sql, *args) tuples per call.
    """

    class _FakeRow(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)

    class _FakePool:
        async def fetch(self, sql, *args):
            if capture_args is not None:
                capture_args.append((sql, args))
            if raise_on_fetch:
                raise RuntimeError("DB exploded")
            return [_FakeRow(r) for r in (fetch_rows or [])]

    return _FakePool()


# ---------------------------------------------------------------------------
# get_taxonomy_trees (multi-KB)
# ---------------------------------------------------------------------------


class TestGetTaxonomyTrees:
    @pytest.mark.asyncio
    async def test_returns_grouped_by_kb_slug(self, monkeypatch):
        """Happy path: rows from multiple KBs get grouped by kb_slug."""
        rows = [
            {
                "id": 1,
                "kb_slug": "support",
                "name": "SSO",
                "node_slug": "sso",
                "parent_id": None,
            },
            {
                "id": 2,
                "kb_slug": "support",
                "name": "SAML",
                "node_slug": "saml",
                "parent_id": 1,
            },
            {
                "id": 10,
                "kb_slug": "billing",
                "name": "Invoices",
                "node_slug": "invoices",
                "parent_id": None,
            },
        ]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_trees

        result = await get_taxonomy_trees("org-1", ["support", "billing"])

        assert set(result.keys()) == {"support", "billing"}
        assert len(result["support"]) == 2
        assert result["support"][0]["id"] == 1
        assert result["support"][0]["kb_slug"] == "support"
        assert result["support"][0]["name"] == "SSO"
        assert result["support"][0]["slug"] == "sso"
        assert result["support"][0]["parent_id"] is None
        assert result["support"][1]["parent_id"] == 1
        assert result["billing"][0]["id"] == 10

    @pytest.mark.asyncio
    async def test_passes_kb_slugs_as_array_param(self, monkeypatch):
        """The query MUST receive kb_slugs as a list for ANY($2::text[])."""
        captured: list = []
        pool = _make_pool_mock(fetch_rows=[], capture_args=captured)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_trees

        await get_taxonomy_trees("org-1", ["support", "billing"])

        assert len(captured) == 1
        _, args = captured[0]
        assert args[0] == "org-1"
        assert args[1] == ["support", "billing"]

    @pytest.mark.asyncio
    async def test_returns_empty_dict_for_empty_input(self, monkeypatch):
        """Empty kb_slugs short-circuits — no DB call."""
        captured: list = []
        pool = _make_pool_mock(fetch_rows=[], capture_args=captured)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_trees

        result = await get_taxonomy_trees("org-1", [])

        assert result == {}
        assert captured == []  # no DB roundtrip

    @pytest.mark.asyncio
    async def test_returns_empty_when_pool_is_none(self, monkeypatch):
        """No pool → fail-open, return {}."""
        monkeypatch.setattr("retrieval_api.services.events._pool", None)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_trees

        result = await get_taxonomy_trees("org-1", ["support"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_db_error(self, monkeypatch):
        """DB exception → fail-open, return {}."""
        pool = _make_pool_mock(raise_on_fetch=True)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_trees

        result = await get_taxonomy_trees("org-1", ["support"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_kb_with_no_nodes_absent_from_result(self, monkeypatch):
        """A KB with zero nodes is simply absent from the result dict."""
        rows = [
            {
                "id": 1,
                "kb_slug": "support",
                "name": "SSO",
                "node_slug": "sso",
                "parent_id": None,
            },
        ]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_taxonomy_trees

        result = await get_taxonomy_trees("org-1", ["support", "billing"])

        assert "support" in result
        assert "billing" not in result


# ---------------------------------------------------------------------------
# get_kb_taxonomy_coverage (multi-KB, binary signal)
# ---------------------------------------------------------------------------


class TestGetKbTaxonomyCoverage:
    @pytest.mark.asyncio
    async def test_binary_signal_one_when_kb_has_nodes(self, monkeypatch):
        """KB with node_count > 0 → coverage 1.0."""
        rows = [
            {"kb_slug": "support", "node_count": 5},
            {"kb_slug": "billing", "node_count": 0},
        ]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", ["support", "billing"])

        assert result == {"support": 1.0, "billing": 0.0}

    @pytest.mark.asyncio
    async def test_missing_kb_defaults_to_zero(self, monkeypatch):
        """KB not in DB result → defaults to 0.0 in returned map."""
        rows = [{"kb_slug": "support", "node_count": 3}]
        pool = _make_pool_mock(fetch_rows=rows)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", ["support", "billing", "unknown"])

        assert result == {"support": 1.0, "billing": 0.0, "unknown": 0.0}

    @pytest.mark.asyncio
    async def test_returns_zeros_when_pool_is_none(self, monkeypatch):
        monkeypatch.setattr("retrieval_api.services.events._pool", None)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", ["support", "billing"])
        assert result == {"support": 0.0, "billing": 0.0}

    @pytest.mark.asyncio
    async def test_returns_zeros_on_db_error(self, monkeypatch):
        """DB exception → fail-open, all-zeros map for the requested slugs."""
        pool = _make_pool_mock(raise_on_fetch=True)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", ["support", "billing"])
        assert result == {"support": 0.0, "billing": 0.0}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_for_empty_input(self, monkeypatch):
        """Empty kb_slugs short-circuits — no DB call, returns {}."""
        captured: list = []
        pool = _make_pool_mock(fetch_rows=[], capture_args=captured)
        monkeypatch.setattr("retrieval_api.services.events._pool", pool)

        from retrieval_api.services.taxonomy_lookup import get_kb_taxonomy_coverage

        result = await get_kb_taxonomy_coverage("org-1", [])

        assert result == {}
        assert captured == []
