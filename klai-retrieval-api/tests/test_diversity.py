"""Tests for source-aware selection (SPEC-KB-021).

source_aware_select() replaces the separate router + quota with one post-rerank
step that uses actual reranker scores to decide source distribution.
"""

import random

import pytest

from retrieval_api.services.diversity import source_aware_select


def _chunk(chunk_id: str, source_label: str | None, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_label": source_label,
        "reranker_score": score,
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

    def test_preserves_final_rank_score_order_when_present(self):
        reranked = [
            {**_chunk("a1", "src-a", 0.95), "final_rank_score": 0.20},
            {**_chunk("b1", "src-b", 0.40), "final_rank_score": 0.90},
            {**_chunk("a2", "src-a", 0.85), "final_rank_score": 0.80},
            {**_chunk("b2", "src-b", 0.30), "final_rank_score": 0.70},
        ]
        selected, _ = source_aware_select(reranked, "query", top_n=4, max_per_source=2)
        assert [c["chunk_id"] for c in selected] == ["b1", "a2", "b2", "a1"]

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


class TestRouterIntegration:
    """Router passes selected sources as boost signal to source_aware_select."""

    def test_router_selected_bypasses_cap(self):
        """Router says 'mitel is relevant' (no keyword in query) → mitel gets all slots."""
        reranked = [
            _chunk("m1", "mitel-help", 0.95),
            _chunk("m2", "mitel-help", 0.90),
            _chunk("m3", "mitel-help", 0.88),
            _chunk("v1", "help.voys.nl", 0.85),
            _chunk("v2", "help.voys.nl", 0.80),
        ]
        # Query does NOT mention "mitel" — but router identified it semantically
        selected, meta = source_aware_select(
            reranked,
            "hoe stel ik een belgroep in",
            top_n=5,
            max_per_source=2,
            router_selected={"mitel-help"},
        )
        assert meta["source_select_mode"] == "router"
        mitel_count = sum(1 for c in selected if c["source_label"] == "mitel-help")
        assert mitel_count == 3  # all mitel chunks, no cap

    def test_router_plus_keyword_combined(self):
        """Both router and keyword detect sources → union of both."""
        reranked = [
            _chunk("m1", "mitel-help", 0.95),
            _chunk("m2", "mitel-help", 0.90),
            _chunk("a1", "ascend-help", 0.85),
            _chunk("a2", "ascend-help", 0.82),
            _chunk("v1", "help.voys.nl", 0.75),
        ]
        # "mitel" in query (keyword), router adds "ascend-help" (semantic)
        _selected, meta = source_aware_select(
            reranked,
            "mitel configuratie",
            top_n=5,
            max_per_source=1,
            router_selected={"ascend-help"},
        )
        assert meta["source_select_mode"] == "keyword+router"
        assert "mitel-help" in meta["mentioned_sources"]
        assert "ascend-help" in meta["mentioned_sources"]

    def test_router_signal_respected_even_with_low_scores(self):
        """A router preference is a tiebreak, not permission to erase score gaps."""
        reranked = [
            _chunk("v1", "help.voys.nl", 0.95),
            _chunk("v2", "help.voys.nl", 0.90),
            _chunk("v3", "help.voys.nl", 0.88),
            _chunk("m1", "mitel-help", 0.30),
            _chunk("m2", "mitel-help", 0.25),
        ]
        selected, meta = source_aware_select(
            reranked,
            "generieke vraag",
            top_n=3,
            max_per_source=2,
            router_selected={"mitel-help"},
        )
        assert meta["source_select_mode"] == "router"
        mitel_ids = [c["chunk_id"] for c in selected if c["source_label"] == "mitel-help"]
        assert mitel_ids == ["m1"]
        assert [chunk["chunk_id"] for chunk in selected[:2]] == ["v1", "v2"]
        assert len(selected) == 3


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


class TestBoundedPreference:
    def test_preference_cannot_invert_a_score_gap_larger_than_boost(self):
        reranked = [
            _chunk("strong", "other", 0.66),
            _chunk("weak", "preferred", 0.28),
        ]

        selected, meta = source_aware_select(
            reranked,
            "query",
            top_n=2,
            max_per_source=2,
            router_selected={"preferred"},
            source_preference_boost=0.05,
        )

        assert [chunk["chunk_id"] for chunk in selected] == ["strong", "weak"]
        assert meta["preference_applied"] is True
        assert meta["preferred_labels"] == ["preferred"]
        assert meta["boost"] == 0.05

    def test_preference_never_inverts_a_gap_larger_than_boost(self):
        rng = random.Random(20260819)  # noqa: S311 - deterministic property test data
        boost = 0.05

        for case in range(250):
            chunks = [
                _chunk(
                    f"{case}-{index}",
                    "preferred" if rng.random() < 0.5 else "other",
                    rng.random(),
                )
                for index in range(rng.randint(2, 20))
            ]
            selected, _ = source_aware_select(
                chunks,
                "query",
                top_n=len(chunks),
                max_per_source=len(chunks),
                router_selected={"preferred"},
                source_preference_boost=boost,
            )
            position = {chunk["chunk_id"]: index for index, chunk in enumerate(selected)}

            for higher in chunks:
                for lower in chunks:
                    if higher["reranker_score"] > lower["reranker_score"] + boost:
                        assert position[higher["chunk_id"]] < position[lower["chunk_id"]]

    def test_counterfactual_reports_no_suppression_for_incident_score_shape(self):
        reranked = [
            _chunk("help-strong", "help.voys.nl", 0.866),
            _chunk("notion-gold", "notion", 0.658),
            _chunk("support-gold", "support", 0.616),
            _chunk("help-weak", "help.voys.nl", 0.280),
            _chunk("help-weaker", "help.voys.nl", 0.208),
        ]

        selected, meta = source_aware_select(
            reranked,
            "Voys trunk 404 Not Found",
            top_n=3,
            max_per_source=3,
            router_selected={"help.voys.nl"},
            source_preference_boost=0.05,
        )

        assert [chunk["chunk_id"] for chunk in selected] == [
            "help-strong",
            "notion-gold",
            "support-gold",
        ]
        assert meta["pack_without_preference"] == [
            "help-strong",
            "notion-gold",
            "support-gold",
        ]
        assert meta["suppressed_count"] == 0
        assert meta["max_score_inversion"] == 0.0

    def test_counterfactual_reports_displacement_and_inversion_gap(self):
        reranked = [
            _chunk("unpreferred", "other", 0.61),
            _chunk("preferred", "preferred", 0.58),
        ]

        selected, meta = source_aware_select(
            reranked,
            "query",
            top_n=1,
            max_per_source=1,
            router_selected={"preferred"},
            source_preference_boost=0.05,
        )

        assert [chunk["chunk_id"] for chunk in selected] == ["preferred"]
        assert meta["pack_without_preference"] == ["unpreferred"]
        assert meta["suppressed_count"] == 1
        assert meta["max_score_inversion"] == pytest.approx(0.03)
