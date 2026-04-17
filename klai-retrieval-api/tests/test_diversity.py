"""Tests for source-aware selection (SPEC-KB-021).

source_aware_select() replaces the separate router + quota with one post-rerank
step that uses actual reranker scores to decide source distribution.
"""

from retrieval_api.services.diversity import source_aware_select


def _chunk(chunk_id: str, source_label: str | None, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_label": source_label,
        "score": score,
        "text": f"c-{chunk_id}",
    }


class TestDiversifyMode:
    """No source mentioned in query → diversify across sources."""

    def test_distributes_across_sources(self):
        reranked = [
            _chunk("a1", "help.voys.nl", 0.95),
            _chunk("a2", "help.voys.nl", 0.90),
            _chunk("a3", "help.voys.nl", 0.88),
            _chunk("b1", "wiki.voys.nl", 0.85),
            _chunk("a4", "help.voys.nl", 0.84),
            _chunk("c1", "mitel-help", 0.82),
            _chunk("b2", "wiki.voys.nl", 0.80),
        ]
        selected, meta = source_aware_select(
            reranked, "hoe maak ik een gebruiker aan", top_n=5, max_per_source=2
        )
        assert len(selected) == 5
        counts = meta["source_counts"]
        for count in counts.values():
            assert count <= 2
        assert meta["source_select_mode"] == "diversify"

    def test_fallback_when_few_sources(self):
        reranked = [_chunk(f"a{i}", "single-source", 0.9 - i * 0.1) for i in range(4)]
        selected, meta = source_aware_select(reranked, "test", top_n=5, max_per_source=2)
        assert len(selected) == 4
        assert meta["source_select_mode"] == "diversify"

    def test_preserves_score_order(self):
        reranked = [
            _chunk("a1", "src-a", 0.95),
            _chunk("b1", "src-b", 0.90),
            _chunk("a2", "src-a", 0.85),
            _chunk("b2", "src-b", 0.80),
        ]
        selected, _ = source_aware_select(reranked, "query", top_n=4, max_per_source=2)
        scores = [c["score"] for c in selected]
        assert scores == sorted(scores, reverse=True)

    def test_empty_input(self):
        selected, meta = source_aware_select([], "query")
        assert selected == []
        assert meta["source_select_mode"] == "empty"


class TestMentionedMode:
    """Source name in query → give that source all slots."""

    def test_single_source_mentioned(self):
        reranked = [
            _chunk("m1", "mitel-help", 0.95),
            _chunk("m2", "mitel-help", 0.90),
            _chunk("m3", "mitel-help", 0.88),
            _chunk("v1", "help.voys.nl", 0.85),
            _chunk("v2", "help.voys.nl", 0.80),
        ]
        selected, meta = source_aware_select(
            reranked, "mitel error X025", top_n=5, max_per_source=2
        )
        assert meta["source_select_mode"] == "mentioned"
        assert "mitel-help" in meta["mentioned_sources"]
        mitel_count = sum(1 for c in selected if c["source_label"] == "mitel-help")
        assert mitel_count == 3  # all mitel chunks, no cap

    def test_multiple_sources_mentioned(self):
        reranked = [
            _chunk("m1", "mitel-help", 0.95),
            _chunk("m2", "mitel-help", 0.90),
            _chunk("a1", "ascend-help", 0.85),
            _chunk("a2", "ascend-help", 0.82),
            _chunk("v1", "help.voys.nl", 0.75),
        ]
        selected, meta = source_aware_select(
            reranked,
            "verschil tussen mitel en ascend",
            top_n=5,
            max_per_source=1,
        )
        assert meta["source_select_mode"] == "mentioned"
        assert "mitel-help" in meta["mentioned_sources"]
        assert "ascend-help" in meta["mentioned_sources"]
        # Both mentioned sources get all their chunks (no cap)
        mitel = sum(1 for c in selected if c["source_label"] == "mitel-help")
        ascend = sum(1 for c in selected if c["source_label"] == "ascend-help")
        assert mitel == 2
        assert ascend == 2

    def test_mentioned_source_fills_with_others_if_short(self):
        """If mentioned source has fewer chunks than top_n, fill with others."""
        reranked = [
            _chunk("m1", "mitel-help", 0.95),
            _chunk("v1", "help.voys.nl", 0.85),
            _chunk("v2", "help.voys.nl", 0.80),
        ]
        selected, _meta = source_aware_select(reranked, "mitel probleem", top_n=3, max_per_source=2)
        assert len(selected) == 3
        assert selected[0]["source_label"] == "mitel-help"  # mentioned first

    def test_short_label_not_detected(self):
        """Labels with len <= 3 should never trigger mention detection."""
        reranked = [
            _chunk("a1", "hr", 0.95),
            _chunk("a2", "hr", 0.90),
            _chunk("b1", "wiki-docs", 0.85),
        ]
        _selected, meta = source_aware_select(reranked, "hr beleid", top_n=2, max_per_source=1)
        assert meta["source_select_mode"] == "diversify"  # not "mentioned"

    def test_stop_words_not_detected(self):
        """Generic words like 'help' should not trigger mention detection."""
        reranked = [
            _chunk("a1", "help.voys.nl", 0.95),
            _chunk("a2", "help.voys.nl", 0.90),
            _chunk("b1", "mitel-help", 0.85),
        ]
        _selected, meta = source_aware_select(
            reranked, "ik heb help nodig", top_n=2, max_per_source=1
        )
        assert meta["source_select_mode"] == "diversify"  # "help" is a stop word


class TestMetadata:
    def test_metadata_keys(self):
        reranked = [_chunk("a1", "src", 0.9)]
        _, meta = source_aware_select(reranked, "query")
        assert "source_select_mode" in meta
        assert "source_counts" in meta
        assert "mentioned_sources" in meta

    def test_source_counts_accurate(self):
        reranked = [
            _chunk("a1", "src-a", 0.95),
            _chunk("a2", "src-a", 0.90),
            _chunk("b1", "src-b", 0.85),
        ]
        _, meta = source_aware_select(reranked, "query", top_n=3, max_per_source=2)
        assert meta["source_counts"]["src-a"] == 2
        assert meta["source_counts"]["src-b"] == 1
