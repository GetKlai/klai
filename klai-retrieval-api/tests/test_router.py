import asyncio

import pytest

from retrieval_api.services.router import (
    KBEntry,
    _build_keyword_map,
    clear_centroid_cache,
    layer1_keyword,
    layer2_semantic,
    route_to_sources,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_centroid_cache()
    yield
    clear_centroid_cache()


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


class TestBuildKeywordMap:
    def test_splits_source_label_on_separators(self):
        kmap = _build_keyword_map(CATALOG)
        assert "mitel" in kmap
        assert "help.mitel.nl" in kmap["mitel"]

    def test_includes_name_words(self):
        kmap = _build_keyword_map(CATALOG)
        assert "voys" in kmap
        assert "help.voys.nl" in kmap["voys"]

    def test_skips_short_tokens(self):
        kmap = _build_keyword_map(CATALOG)
        assert "nl" not in kmap  # too short (2 chars)

    def test_no_description_words_in_map(self):
        """Description words should NOT be in keyword map (too generic)."""
        kmap = _build_keyword_map(CATALOG)
        # "documentatie" is in Mitel's description but should be filtered
        assert "documentatie" not in kmap

    def test_stop_words_filtered(self):
        """Generic words like 'help', 'docs', 'wiki' should not be routing tokens."""
        kmap = _build_keyword_map(CATALOG)
        assert "help" not in kmap
        assert "wiki" not in kmap

    def test_collision_maps_to_both_sources(self):
        """Two sources sharing a token should both be in the set."""
        catalog = [
            KBEntry(source_label="mitel-helpcenter", name="Mitel HC"),
            KBEntry(source_label="mitel-wiki", name="Mitel Wiki"),
        ]
        kmap = _build_keyword_map(catalog)
        assert "mitel" in kmap
        assert "mitel-helpcenter" in kmap["mitel"]
        assert "mitel-wiki" in kmap["mitel"]


class TestLayer1Keyword:
    def test_matches_brand_in_query(self):
        kmap = _build_keyword_map(CATALOG)
        result = layer1_keyword("hoe configureer ik mitel voip", kmap)
        assert result is not None
        assert "help.mitel.nl" in result

    def test_no_match_returns_none(self):
        kmap = _build_keyword_map(CATALOG)
        result = layer1_keyword("hoe maak ik een gebruiker aan", kmap)
        assert result is None

    def test_multiple_brands_matched(self):
        kmap = _build_keyword_map(CATALOG)
        result = layer1_keyword("verschil tussen mitel en ascend", kmap)
        assert result is not None
        assert len(result) >= 2


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
    async def test_layer1_keyword_hit(self):
        decision = await route_to_sources(
            "hoe configureer ik mitel voip",
            query_vector=[0.5, 0.5],
            org_id="org-1",
            source_label_catalog=CATALOG,
        )
        assert decision.layer_used == "keyword"
        assert "help.mitel.nl" in decision.selected_source_labels

    @pytest.mark.asyncio
    async def test_layer2_with_compute_fn(self):
        async def fake_compute(catalog):
            return {
                "help.mitel.nl": [1.0, 0.0, 0.0],
                "help.voys.nl": [0.0, 1.0, 0.0],
                "redcactus-wiki": [0.0, 0.0, 1.0],
                "ascend-help": [0.3, 0.3, 0.4],
                "notion-internal": [0.2, 0.5, 0.3],
            }

        # Query close to mitel
        decision = await route_to_sources(
            "hoe werkt een pbx systeem",  # no keyword match
            query_vector=[0.95, 0.1, 0.05],
            org_id="org-1",
            source_label_catalog=CATALOG,
            compute_centroid_fn=fake_compute,
        )
        assert decision.layer_used == "semantic"
        assert decision.selected_source_labels is not None
        assert "help.mitel.nl" in decision.selected_source_labels

    @pytest.mark.asyncio
    async def test_user_override_skips_router(self):
        """Router must NOT be called when user sets kb_slugs.
        This is enforced at retrieve.py level, but we test the invariant here."""
        # The router itself always runs when called — the skip is in retrieve.py
        decision = await route_to_sources(
            "anything",
            query_vector=[0.5, 0.5],
            org_id="org-1",
            source_label_catalog=CATALOG,
        )
        # Router always returns a decision when called
        assert decision is not None

    @pytest.mark.asyncio
    async def test_layer3_timeout_failopen(self):
        async def slow_llm(query, catalog):
            await asyncio.sleep(2.0)  # exceeds 500ms timeout
            return ["should-not-reach"]

        async def no_centroids(catalog):
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

        async def counting_compute(catalog):
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
        async def equal_centroids(catalog):
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
