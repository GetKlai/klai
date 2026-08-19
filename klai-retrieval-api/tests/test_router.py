import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.services.router import (
    KBEntry,
    _catalog_cache,
    _centroid_cache,
    clear_catalog_cache,
    clear_centroid_cache,
    fetch_source_catalog,
    layer2_semantic,
    route_to_sources,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_centroid_cache()
    clear_catalog_cache()
    yield
    clear_centroid_cache()
    clear_catalog_cache()


CATALOG = [
    KBEntry(
        source_label="help.mitel.nl",
        name="Mitel Helpcenter",
        description="Documentatie voor Mitel-telefoons",
    ),
    KBEntry(
        source_label="help.voys.nl", name="Voys Helpdesk", description="Voys klantondersteuning"
    ),
    KBEntry(
        source_label="redcactus-wiki",
        name="Redcactus Wiki",
        description="Interne Redcactus documentatie",
    ),
    KBEntry(
        source_label="ascend-help", name="Ascend Helpcenter", description="Ascend telefoniedocs"
    ),
    KBEntry(source_label="notion-internal", name="Notion Wiki", description="Interne bedrijfswiki"),
]


class TestLayer2Semantic:
    def test_single_route_high_margin(self):
        centroids = {
            "source-a": [1.0, 0.0, 0.0],
            "source-b": [0.0, 1.0, 0.0],
            "source-c": [0.0, 0.0, 1.0],
        }
        query = [0.95, 0.1, 0.05]  # very close to source-a
        selected, margin = layer2_semantic(query, centroids, margin_single=0.15, margin_dual=0.08)
        assert selected == ["source-a"]
        assert margin is not None
        assert margin > 0.15

    def test_dual_route_medium_margin(self):
        centroids = {
            "source-a": [1.0, 0.0],
            "source-b": [0.8, 0.6],  # close to query but less so than source-a
            "source-c": [0.0, 1.0],
        }
        query = [0.95, 0.3]
        # margin ~0.010, above dual threshold 0.01 but below single threshold 0.50
        selected, _margin = layer2_semantic(query, centroids, margin_single=0.50, margin_dual=0.01)
        assert selected is not None
        assert len(selected) == 2

    def test_no_route_low_margin(self):
        centroids = {
            "source-a": [1.0, 0.0],
            "source-b": [0.99, 0.14],  # almost identical to a
        }
        query = [1.0, 0.07]
        selected, _margin = layer2_semantic(query, centroids, margin_single=0.5, margin_dual=0.4)
        assert selected is None

    def test_empty_centroids(self):
        selected, margin = layer2_semantic([1.0, 0.0], {})
        assert selected is None
        assert margin is None


class TestRouteToSources:
    @pytest.mark.asyncio
    async def test_brand_name_does_not_override_semantic_abstention(self):
        async def near_tie_centroids(catalog, org_id):
            return {
                "help.mitel.nl": [1.0, 0.0],
                "help.voys.nl": [0.999, 0.001],
                "redcactus-wiki": [0.998, 0.002],
            }

        decision = await route_to_sources(
            "hoe configureer ik mitel voip",
            query_vector=[1.0, 0.0],
            org_id="org-1",
            source_label_catalog=CATALOG[:3],
            compute_centroid_fn=near_tie_centroids,
        )
        assert decision.layer_used == "none"
        assert decision.selected_source_labels is None

    @pytest.mark.asyncio
    async def test_layer2_with_compute_fn(self):
        async def fake_compute(catalog, org_id):
            return {
                "help.mitel.nl": [1.0, 0.0, 0.0],
                "help.voys.nl": [0.0, 1.0, 0.0],
                "redcactus-wiki": [0.0, 0.0, 1.0],
                "ascend-help": [0.3, 0.3, 0.4],
                "notion-internal": [0.2, 0.5, 0.3],
            }

        # Query close to Mitel; semantic similarity is the only routing signal.
        decision = await route_to_sources(
            "hoe werkt een pbx systeem",
            query_vector=[0.95, 0.1, 0.05],
            org_id="org-1",
            source_label_catalog=CATALOG,
            compute_centroid_fn=fake_compute,
        )
        assert decision.layer_used == "semantic"
        assert decision.selected_source_labels is not None
        assert "help.mitel.nl" in decision.selected_source_labels

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_layer3_timeout_failopen(self):
        async def slow_llm(query, catalog):
            await asyncio.sleep(2.0)  # exceeds 500ms timeout
            return ["should-not-reach"]

        async def no_centroids(catalog, org_id):
            return {}  # force layer 2 to produce no result

        decision = await route_to_sources(
            "vage vraag zonder keywords",
            query_vector=[0.33, 0.33, 0.34],
            org_id="org-1",
            source_label_catalog=CATALOG,
            llm_fallback=True,
            llm_fn=slow_llm,
            compute_centroid_fn=no_centroids,
        )
        assert decision.selected_source_labels is None  # fail-open
        assert decision.layer_used in ("llm", "none")

    @pytest.mark.asyncio
    async def test_centroid_cache_hit(self):
        call_count = 0

        async def counting_compute(catalog, org_id):
            nonlocal call_count
            call_count += 1
            return {"a": [1.0, 0.0], "b": [0.0, 1.0]}

        # First call: cache miss
        await route_to_sources(
            "query1", [0.9, 0.1], "org-cache", CATALOG[:2], compute_centroid_fn=counting_compute
        )
        assert call_count == 1

        # Second call: cache hit (same org)
        decision = await route_to_sources(
            "query2", [0.9, 0.1], "org-cache", CATALOG[:2], compute_centroid_fn=counting_compute
        )
        assert call_count == 1  # not called again
        assert decision.cache_hit is True

    @pytest.mark.asyncio
    async def test_no_route_returns_none(self):
        async def equal_centroids(catalog, org_id):
            return {
                "a": [0.5, 0.5],
                "b": [0.5, 0.5],
                "c": [0.5, 0.5],
                "d": [0.5, 0.5],
            }

        decision = await route_to_sources(
            "hele generieke vraag",
            query_vector=[0.5, 0.5],
            org_id="org-1",
            source_label_catalog=CATALOG[:4],
            compute_centroid_fn=equal_centroids,
        )
        assert decision.selected_source_labels is None
        assert decision.layer_used == "none"


class TestSourceCatalogScope:
    @pytest.mark.asyncio
    async def test_pinned_kb_filter_and_cache_key_are_scoped(self):
        mock_client = AsyncMock()
        mock_client.facet.return_value = SimpleNamespace(hits=[])

        with patch("retrieval_api.services.search._get_client", return_value=mock_client):
            await fetch_source_catalog("org-1", ["sip"])
            await fetch_source_catalog("org-1")

        assert mock_client.facet.await_count == 2
        pinned_filter = mock_client.facet.await_args_list[0].kwargs["facet_filter"]
        pinned_conditions = {condition.key: condition.match for condition in pinned_filter.must}
        assert pinned_conditions["org_id"].value == "org-1"
        assert pinned_conditions["kb_slug"].any == ["sip"]
        assert ("org-1", ("sip",)) in _catalog_cache
        assert ("org-1", None) in _catalog_cache

    @pytest.mark.asyncio
    async def test_transient_facet_failure_is_not_cached(self):
        mock_client = AsyncMock()
        mock_client.facet.side_effect = [
            RuntimeError("temporary qdrant failure"),
            SimpleNamespace(hits=[SimpleNamespace(value="notion")]),
        ]

        with patch("retrieval_api.services.search._get_client", return_value=mock_client):
            assert await fetch_source_catalog("org-1") == []
            recovered = await fetch_source_catalog("org-1")

        assert [entry.source_label for entry in recovered] == ["notion"]
        assert mock_client.facet.await_count == 2

    @pytest.mark.asyncio
    async def test_router_caches_are_bounded(self, monkeypatch):
        monkeypatch.setattr("retrieval_api.services.router._ROUTER_CACHE_MAX_ENTRIES", 2)
        mock_client = AsyncMock()
        mock_client.facet.return_value = SimpleNamespace(hits=[])

        with patch("retrieval_api.services.search._get_client", return_value=mock_client):
            for org_id in ("org-1", "org-2", "org-3"):
                await fetch_source_catalog(org_id)

        async def compute(catalog, org_id):
            return {"notion": [1.0, 0.0]}

        catalog = [KBEntry(source_label="notion", name="Notion")]
        for org_id in ("org-1", "org-2", "org-3"):
            await route_to_sources(
                "query",
                [1.0, 0.0],
                org_id,
                catalog,
                compute_centroid_fn=compute,
            )

        assert len(_catalog_cache) <= 2
        assert len(_centroid_cache) <= 2

    def test_catalog_cache_can_be_cleared_per_org(self):
        _catalog_cache[("org-1", None)] = ([], 1.0)
        _catalog_cache[("org-2", None)] = ([], 1.0)

        clear_catalog_cache("org-1")

        assert ("org-1", None) not in _catalog_cache
        assert ("org-2", None) in _catalog_cache
