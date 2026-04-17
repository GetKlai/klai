"""RED: Verify per-source_label quota selection after rerank.

SPEC-KB-021 Change 2:
- source_quota_select() applies max_per_source quota to reranked chunks
- bypass_on_mention: if source_label substring appears in query, that source bypasses quota
- Short source labels (len <= 3) never trigger bypass
- Fallback fills remaining slots from leftovers when not enough unique sources
- Returns (selected_chunks, metadata) tuple
"""

from retrieval_api.services.diversity import source_quota_select


def _make_chunk(chunk_id: str, source_label: str | None, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "source_label": source_label,
        "score": score,
        "text": f"chunk {chunk_id}",
    }


class TestSourceQuotaSelect:
    def test_happy_path_distributes_across_sources(self):
        """6 chunks from source A, 2 from B, 2 from C → max 2 per source in top 5."""
        reranked = [
            _make_chunk("a1", "help.voys.nl", 0.95),
            _make_chunk("a2", "help.voys.nl", 0.90),
            _make_chunk("a3", "help.voys.nl", 0.88),
            _make_chunk("b1", "wiki.voys.nl", 0.85),
            _make_chunk("a4", "help.voys.nl", 0.84),
            _make_chunk("c1", "mitel-help", 0.82),
            _make_chunk("b2", "wiki.voys.nl", 0.80),
            _make_chunk("a5", "help.voys.nl", 0.78),
            _make_chunk("c2", "mitel-help", 0.75),
            _make_chunk("a6", "help.voys.nl", 0.70),
        ]
        selected, meta = source_quota_select(
            reranked, "hoe maak ik een gebruiker aan", top_n=5, max_per_source=2
        )
        assert len(selected) == 5
        source_counts: dict[str, int] = {}
        for c in selected:
            sl = c["source_label"]
            source_counts[sl] = source_counts.get(sl, 0) + 1
        for count in source_counts.values():
            assert count <= 2
        assert meta["quota_applied"] is True

    def test_per_source_label_bypass_on_mention(self):
        """Query mentions 'mitel' → mitel-help bypasses quota, others keep limit."""
        reranked = [
            _make_chunk("m1", "mitel-help", 0.95),
            _make_chunk("m2", "mitel-help", 0.90),
            _make_chunk("m3", "mitel-help", 0.88),
            _make_chunk("v1", "help.voys.nl", 0.85),
            _make_chunk("v2", "help.voys.nl", 0.80),
            _make_chunk("v3", "help.voys.nl", 0.75),
        ]
        selected, meta = source_quota_select(
            reranked, "mitel error X025 oplossen", top_n=5, max_per_source=2
        )
        mitel_count = sum(1 for c in selected if c["source_label"] == "mitel-help")
        assert mitel_count == 3  # bypassed, all 3 included
        voys_count = sum(1 for c in selected if c["source_label"] == "help.voys.nl")
        assert voys_count <= 2  # quota still enforced
        assert "mitel-help" in meta["quota_bypass_source_labels"]

    def test_multi_source_bypass_on_mention(self):
        """Query mentions both 'mitel' and 'ascend' → both bypass quota."""
        reranked = [
            _make_chunk("m1", "mitel-help", 0.95),
            _make_chunk("m2", "mitel-help", 0.90),
            _make_chunk("m3", "mitel-help", 0.88),
            _make_chunk("a1", "ascend-help", 0.85),
            _make_chunk("a2", "ascend-help", 0.82),
            _make_chunk("a3", "ascend-help", 0.80),
            _make_chunk("v1", "help.voys.nl", 0.75),
        ]
        selected, meta = source_quota_select(
            reranked,
            "verschil tussen mitel en ascend",
            top_n=5,
            max_per_source=1,
        )
        mitel_count = sum(1 for c in selected if c["source_label"] == "mitel-help")
        ascend_count = sum(1 for c in selected if c["source_label"] == "ascend-help")
        assert mitel_count >= 2  # bypassed
        assert ascend_count >= 2  # also bypassed
        assert "mitel-help" in meta["quota_bypass_source_labels"]
        assert "ascend-help" in meta["quota_bypass_source_labels"]

    def test_fallback_underfill(self):
        """Only 4 chunks, all same source → return all 4, no error."""
        reranked = [_make_chunk(f"a{i}", "single-source", 0.9 - i * 0.1) for i in range(4)]
        selected, meta = source_quota_select(reranked, "test query", top_n=5, max_per_source=2)
        assert len(selected) == 4  # can't fill 5, return what we have
        assert meta["quota_applied"] is True

    def test_short_source_label_not_bypassed(self):
        """source_label with len <= 3 should not trigger bypass even if in query.

        Use enough unique sources (3+) so top_n=3 can be filled without fallback.
        """
        reranked = [
            _make_chunk("a1", "hr", 0.95),
            _make_chunk("a2", "hr", 0.90),
            _make_chunk("a3", "hr", 0.85),
            _make_chunk("b1", "wiki", 0.80),
            _make_chunk("c1", "intranet", 0.75),
            _make_chunk("d1", "manual", 0.70),
        ]
        selected, _meta = source_quota_select(reranked, "hr beleid", top_n=3, max_per_source=1)
        hr_count = sum(1 for c in selected if c["source_label"] == "hr")
        assert hr_count <= 1  # no bypass for short labels

    def test_no_source_label_uses_unknown(self):
        """Chunks without source_label (None) are grouped under '_unknown' for quota counting.

        With only 2 unique source groups (None→_unknown, wiki) and top_n=3,
        the fallback must fill the remaining slot from leftover — so 2 None-chunks
        end up in the result. That is correct: fallback fills when there are no
        other sources available.
        """
        reranked = [
            _make_chunk("a1", None, 0.95),
            _make_chunk("a2", None, 0.90),
            _make_chunk("b1", "wiki", 0.85),
        ]
        selected, meta = source_quota_select(reranked, "query", top_n=3, max_per_source=1)
        # quota_per_source_counts uses '_unknown' key for None labels
        counts = meta["quota_per_source_counts"]
        assert "_unknown" in counts
        assert len(selected) == 3  # fallback fills all slots

    def test_bypass_disabled(self):
        """bypass_on_mention=False → no bypass even if source in query.

        Use enough unique sources (3+) so top_n=3 can be filled without fallback.
        """
        reranked = [
            _make_chunk("m1", "mitel-help", 0.95),
            _make_chunk("m2", "mitel-help", 0.90),
            _make_chunk("m3", "mitel-help", 0.85),
            _make_chunk("v1", "help.voys.nl", 0.80),
            _make_chunk("w1", "wiki.voys.nl", 0.75),
            _make_chunk("x1", "support-docs", 0.70),
        ]
        selected, meta = source_quota_select(
            reranked, "mitel error", top_n=3, max_per_source=1, bypass_on_mention=False
        )
        mitel_count = sum(1 for c in selected if c["source_label"] == "mitel-help")
        assert mitel_count <= 1
        assert meta["quota_bypass_source_labels"] == []

    def test_metadata_structure(self):
        """Returned metadata must contain required keys."""
        reranked = [_make_chunk("a1", "help.voys.nl", 0.9)]
        _, meta = source_quota_select(reranked, "query", top_n=5, max_per_source=2)
        assert "quota_applied" in meta
        assert "quota_per_source_counts" in meta
        assert "quota_bypass_reason" in meta
        assert "quota_bypass_source_labels" in meta

    def test_empty_input(self):
        """Empty reranked list → empty selection, no error."""
        selected, meta = source_quota_select([], "query", top_n=5, max_per_source=2)
        assert selected == []
        assert meta["quota_applied"] is True

    def test_exact_top_n_no_overflow(self):
        """When exactly top_n chunks are available → all returned."""
        reranked = [
            _make_chunk("a1", "src-a", 0.9),
            _make_chunk("b1", "src-b", 0.8),
            _make_chunk("c1", "src-c", 0.7),
        ]
        selected, _ = source_quota_select(reranked, "query", top_n=3, max_per_source=2)
        assert len(selected) == 3

    def test_order_preserved_by_score(self):
        """Selected chunks should remain in descending score order."""
        reranked = [
            _make_chunk("a1", "help.voys.nl", 0.95),
            _make_chunk("b1", "wiki.voys.nl", 0.90),
            _make_chunk("a2", "help.voys.nl", 0.85),
            _make_chunk("b2", "wiki.voys.nl", 0.80),
            _make_chunk("a3", "help.voys.nl", 0.75),
        ]
        selected, _ = source_quota_select(reranked, "query", top_n=4, max_per_source=2)
        scores = [c["score"] for c in selected]
        assert scores == sorted(scores, reverse=True)

    def test_per_source_counts_in_metadata(self):
        """quota_per_source_counts should reflect actual selection counts."""
        reranked = [
            _make_chunk("a1", "help.voys.nl", 0.95),
            _make_chunk("a2", "help.voys.nl", 0.90),
            _make_chunk("b1", "wiki.voys.nl", 0.85),
        ]
        _selected, meta = source_quota_select(reranked, "query", top_n=3, max_per_source=2)
        counts = meta["quota_per_source_counts"]
        assert counts.get("help.voys.nl", 0) == 2
        assert counts.get("wiki.voys.nl", 0) == 1
