"""Tests for gap classification logic (SPEC-KB-015).

Pure unit tests -- no DB. Mirrors _classify_gap from deploy/litellm/klai_knowledge.py.
"""

from unittest.mock import patch

from app.core.config import settings


class TestClassifyGap:
    """Test classify_gap function with various chunk scenarios."""

    def test_classify_gap_hard(self) -> None:
        """Empty chunks list returns 'hard' gap."""
        from app.services.gap_classification import classify_gap

        assert classify_gap([]) == "hard"

    def test_classify_gap_soft_reranker(self) -> None:
        """All reranker_scores below threshold returns 'soft'."""
        from app.services.gap_classification import classify_gap

        chunks = [
            {"reranker_score": 0.1, "score": 0.8},
            {"reranker_score": 0.2, "score": 0.9},
            {"reranker_score": 0.3, "score": 0.7},
        ]
        assert classify_gap(chunks) == "soft"

    def test_classify_gap_not_a_gap_reranker(self) -> None:
        """At least one reranker_score at or above threshold returns None (not a gap)."""
        from app.services.gap_classification import classify_gap

        chunks = [
            {"reranker_score": 0.1, "score": 0.8},
            {"reranker_score": 0.5, "score": 0.9},  # >= 0.4 threshold
        ]
        assert classify_gap(chunks) is None

    def test_classify_gap_soft_dense(self) -> None:
        """No reranker scores, all dense scores below threshold returns 'soft'."""
        from app.services.gap_classification import classify_gap

        chunks = [
            {"score": 0.1},
            {"score": 0.2},
            {"score": 0.3},
        ]
        assert classify_gap(chunks) == "soft"

    def test_classify_gap_not_a_gap_dense(self) -> None:
        """No reranker scores, at least one dense score at or above threshold returns None."""
        from app.services.gap_classification import classify_gap

        chunks = [
            {"score": 0.1},
            {"score": 0.5},  # >= 0.35 threshold
        ]
        assert classify_gap(chunks) is None

    def test_classify_gap_threshold_env_override(self) -> None:
        """Custom thresholds from settings are respected."""
        from app.services.gap_classification import classify_gap

        # With default threshold 0.4, a reranker_score of 0.39 is "soft"
        chunks = [{"reranker_score": 0.39}]
        assert classify_gap(chunks) == "soft"

        # Override threshold to 0.3 -- now 0.39 is above threshold, so not a gap
        with patch.object(settings, "klai_gap_soft_threshold", 0.3):
            assert classify_gap(chunks) is None

    def test_classify_gap_reranker_at_exact_threshold(self) -> None:
        """Reranker score exactly at threshold is NOT a gap (only strictly below is soft)."""
        from app.services.gap_classification import classify_gap

        chunks = [{"reranker_score": 0.4}]  # exactly at default threshold
        assert classify_gap(chunks) is None

    def test_classify_gap_dense_at_exact_threshold(self) -> None:
        """Dense score exactly at threshold is NOT a gap."""
        from app.services.gap_classification import classify_gap

        chunks = [{"score": 0.35}]  # exactly at default threshold
        assert classify_gap(chunks) is None

    def test_classify_gap_mixed_reranker_none_and_present(self) -> None:
        """Only chunks with reranker_score are considered for reranker path."""
        from app.services.gap_classification import classify_gap

        # One chunk has reranker_score, one does not. Reranker path is used
        # because at least one reranker_score is present.
        chunks = [
            {"reranker_score": 0.1, "score": 0.8},
            {"score": 0.9},  # no reranker_score -- excluded from reranker list
        ]
        # reranker_scores = [0.1], all < 0.4 => soft
        assert classify_gap(chunks) == "soft"

    def test_classify_gap_dense_fallback_defaults_to_zero(self) -> None:
        """Chunks without 'score' key default to 0.0 for dense path."""
        from app.services.gap_classification import classify_gap

        chunks = [{"some_other_key": "value"}]  # no reranker_score, no score
        # dense_scores = [0.0], all < 0.35 => soft
        assert classify_gap(chunks) == "soft"
