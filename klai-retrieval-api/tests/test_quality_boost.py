"""Verify quality score boost after RRF+rerank.

SPEC-KB-015 REQ-KB-015-19 through REQ-KB-015-21:
- feedback_count >= 3 → boost: base * (1 + 0.2 * (quality_score - 0.5))
- feedback_count < 3 → no boost (cold start guard)
- Missing fields → defaults quality_score=0.5, feedback_count=0 → no boost

SPEC-RAG-EVIDENCE-INTEGRITY-001 REQ-RANK-03/04 — mode split:
- ``contract_active=False`` (ranking-contract shadow, the pipeline default)
  MUST be the exact pre-contract behavior: boost mutates ``score`` and the
  list is ALWAYS re-sorted by ``score``.
- ``contract_active=True`` mutates ``final_rank_score`` and re-sorts only
  when at least one chunk was actually boosted.
"""

import pytest

from retrieval_api.quality_boost import quality_boost


def apply_quality_boost(reranked: list[dict]) -> list[dict]:
    """Default call — ranking-contract shadow (pre-contract behavior)."""
    return quality_boost(reranked)


class TestQualityBoostFormulaShadow:
    """Shadow mode: the boost formula mutates the raw ``score`` (legacy)."""

    def test_positive_boost_high_quality(self):
        """feedback_count >= 3, quality_score=0.75 → score * 1.05"""
        chunks = [{"score": 1.0, "feedback_count": 3, "quality_score": 0.75}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(1.0 * (1 + 0.2 * 0.25), abs=1e-9)
        assert "final_rank_score" not in result[0]

    def test_negative_boost_low_quality(self):
        """feedback_count >= 3, quality_score=0.25 → score * 0.95"""
        chunks = [{"score": 1.0, "feedback_count": 3, "quality_score": 0.25}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(1.0 * (1 + 0.2 * -0.25), abs=1e-9)

    def test_neutral_no_change(self):
        """feedback_count >= 3, quality_score=0.5 → score unchanged"""
        chunks = [{"score": 0.8, "feedback_count": 5, "quality_score": 0.5}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)

    def test_running_average_example(self):
        """75% positive with 3 feedbacks, then thumbsDown → 0.5625 quality"""
        chunks = [{"score": 0.9, "feedback_count": 4, "quality_score": 0.5625}]
        result = apply_quality_boost(chunks)
        expected = 0.9 * (1 + 0.2 * (0.5625 - 0.5))
        assert result[0]["score"] == pytest.approx(expected, abs=1e-9)


class TestQualityBoostFormulaActive:
    """Active mode: the boost mutates ``final_rank_score`` (REQ-RANK-03)."""

    def test_positive_boost_targets_final_rank_score(self):
        chunks = [
            {"score": 0.1, "final_rank_score": 1.0, "feedback_count": 3, "quality_score": 0.75}
        ]
        result = quality_boost(chunks, contract_active=True)
        assert result[0]["final_rank_score"] == pytest.approx(1.0 * (1 + 0.2 * 0.25), abs=1e-9)
        # The raw score is untouched in active mode.
        assert result[0]["score"] == pytest.approx(0.1, abs=1e-9)

    def test_negative_boost_targets_final_rank_score(self):
        chunks = [
            {"score": 0.1, "final_rank_score": 1.0, "feedback_count": 3, "quality_score": 0.25}
        ]
        result = quality_boost(chunks, contract_active=True)
        assert result[0]["final_rank_score"] == pytest.approx(1.0 * (1 + 0.2 * -0.25), abs=1e-9)


class TestColdStartGuard:
    """REQ-KB-015-20: feedback_count < 3 → no boost"""

    def test_feedback_count_zero(self):
        chunks = [{"score": 0.8, "feedback_count": 0, "quality_score": 1.0}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)
        assert "final_rank_score" not in result[0]

    def test_feedback_count_one(self):
        chunks = [{"score": 0.8, "feedback_count": 1, "quality_score": 1.0}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)

    def test_feedback_count_two(self):
        chunks = [{"score": 0.8, "feedback_count": 2, "quality_score": 0.0}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)

    def test_feedback_count_three_applies_boost(self):
        """Exact threshold: 3 should apply boost"""
        chunks = [{"score": 0.8, "feedback_count": 3, "quality_score": 1.0}]
        result = apply_quality_boost(chunks)
        expected = 0.8 * (1 + 0.2 * 0.5)  # 0.88
        assert result[0]["score"] == pytest.approx(expected, abs=1e-9)


class TestMissingFields:
    """REQ-KB-015-21: Missing fields → defaults, no boost"""

    def test_missing_quality_score(self):
        """Missing quality_score → default 0.5, neutral → no boost"""
        chunks = [{"score": 0.8, "feedback_count": 5}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)

    def test_missing_feedback_count(self):
        """Missing feedback_count → default 0, no boost (cold start)"""
        chunks = [{"score": 0.8, "quality_score": 1.0}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)

    def test_missing_both_fields(self):
        """Missing both → defaults → no boost"""
        chunks = [{"score": 0.8}]
        result = apply_quality_boost(chunks)
        assert result[0]["score"] == pytest.approx(0.8, abs=1e-9)


class TestReSort:
    """Verify re-sort semantics per mode."""

    def test_high_quality_chunk_moves_up(self):
        """Shadow: a lower-scored high-quality chunk moves above a penalized one."""
        chunks = [
            {"score": 0.9, "feedback_count": 10, "quality_score": 0.1},  # penalized
            {"score": 0.85, "feedback_count": 10, "quality_score": 0.9},  # boosted
        ]
        result = apply_quality_boost(chunks)
        # Chunk 2 (0.85 * 1.08 = 0.918) should be above Chunk 1 (0.9 * 0.92 = 0.828)
        assert result[0]["score"] > result[1]["score"]
        assert result[0]["quality_score"] == 0.9

    def test_shadow_no_votes_still_resorts_by_score(self):
        """Pre-contract behavior (REQ-RANK-04): the legacy path ALWAYS
        re-sorts by raw score, boost or not — shadow serving must keep it."""
        chunks = [
            {"chunk_id": "a", "score": 0.2, "feedback_count": 0},
            {"chunk_id": "b", "score": 0.9, "feedback_count": 0},
        ]
        result = apply_quality_boost(chunks)
        assert [chunk["chunk_id"] for chunk in result] == ["b", "a"]

    def test_active_high_quality_chunk_moves_up_on_final_rank_score(self):
        chunks = [
            {"score": 0.01, "final_rank_score": 0.9, "feedback_count": 10, "quality_score": 0.1},
            {"score": 0.02, "final_rank_score": 0.85, "feedback_count": 10, "quality_score": 0.9},
        ]
        result = quality_boost(chunks, contract_active=True)
        assert result[0]["final_rank_score"] > result[1]["final_rank_score"]
        assert result[0]["quality_score"] == 0.9

    def test_active_no_feedback_votes_preserves_input_order(self):
        """REQ-RANK-03: active mode does NOT re-sort when nothing was boosted."""
        chunks = [
            {"chunk_id": "a", "score": 0.2, "final_rank_score": 0.2, "feedback_count": 0},
            {"chunk_id": "b", "score": 0.9, "final_rank_score": 0.9, "feedback_count": 0},
        ]
        result = quality_boost(chunks, contract_active=True)
        assert [chunk["chunk_id"] for chunk in result] == ["a", "b"]

    def test_empty_list(self):
        """Empty input should return empty output"""
        result = apply_quality_boost([])
        assert result == []
