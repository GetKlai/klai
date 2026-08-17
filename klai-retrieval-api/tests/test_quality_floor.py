"""Quality-floor filter tests for SPEC-INGEST-LOGIN-WALL-DETECT-001 Phase E.

REQ-07: defence-in-depth filter that removes chunks whose ``quality_score``
falls below ``KLAI_RETRIEVAL_QUALITY_FLOOR`` (default ``0.05``). Catches
chunks that slipped past the ingest-time detector or were marked by the
``degrade`` mode.

The default threshold (0.05) is intentionally chosen so that:
- Default ``quality_score=0.5`` chunks ALWAYS pass.
- Only chunks that someone explicitly degraded to ``0.0`` get filtered.
- Minor floating-point drift (e.g., ``0.49999...``) does NOT trigger filter.
"""

from __future__ import annotations

import pytest

from retrieval_api import quality_boost, quality_floor
from retrieval_api.quality_floor import filter_quality_floor


class TestFilterQualityFloor:
    def test_empty_input(self) -> None:
        out, n_filtered = filter_quality_floor([], floor=0.05)
        assert out == []
        assert n_filtered == 0

    def test_zero_quality_score_filtered_at_default_floor(self) -> None:
        chunks = [
            {"chunk_id": "good-1", "quality_score": 0.5, "score": 0.8},
            {"chunk_id": "bad-1", "quality_score": 0.0, "score": 0.85},
            {"chunk_id": "good-2", "quality_score": 0.5, "score": 0.7},
        ]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 1
        out_ids = [c["chunk_id"] for c in out]
        assert "bad-1" not in out_ids
        assert "good-1" in out_ids
        assert "good-2" in out_ids

    def test_default_quality_score_always_passes(self) -> None:
        """REQ-07 AC-07.2: chunks at the existing default 0.5 are NEVER filtered
        at any reasonable floor. Confirms no regression on existing data."""
        chunks = [{"chunk_id": "neutral", "quality_score": 0.5, "score": 0.6}]
        for floor in (0.01, 0.05, 0.1, 0.49):
            out, n_filtered = filter_quality_floor(chunks, floor=floor)
            assert n_filtered == 0
            assert out == chunks

    def test_floor_threshold_inclusive_at_boundary(self) -> None:
        """Chunk at exactly the floor should PASS (>=, not >)."""
        chunks = [
            {"chunk_id": "boundary", "quality_score": 0.05, "score": 0.5},
        ]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 0
        assert len(out) == 1

    def test_missing_quality_score_treated_as_default(self) -> None:
        """Chunks without a quality_score field use the existing 0.5 default
        (qdrant_store hard-coded). Filter MUST NOT remove them."""
        chunks = [
            {"chunk_id": "legacy", "score": 0.5},  # no quality_score
            {"chunk_id": "explicit-zero", "quality_score": 0.0, "score": 0.5},
        ]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        out_ids = [c["chunk_id"] for c in out]
        assert "legacy" in out_ids, "missing quality_score should default to 0.5 and pass filter"
        assert "explicit-zero" not in out_ids
        assert n_filtered == 1

    def test_high_floor_removes_default_chunks(self) -> None:
        """REQ-07 AC-07.3: floor is configurable. At 0.6, default 0.5 chunks
        are filtered — the operator-tunable lever exists."""
        chunks = [
            {"chunk_id": "default", "quality_score": 0.5, "score": 0.5},
            {"chunk_id": "high", "quality_score": 0.8, "score": 0.5},
        ]
        out, n_filtered = filter_quality_floor(chunks, floor=0.6)
        out_ids = [c["chunk_id"] for c in out]
        assert out_ids == ["high"]
        assert n_filtered == 1

    def test_preserves_input_order(self) -> None:
        """Filter MUST NOT reorder. Re-ranking is the responsibility of
        upstream (rerank) / downstream (quality_boost) — the floor just
        drops bad chunks in place."""
        chunks = [
            {"chunk_id": "a", "quality_score": 0.5, "score": 0.9},
            {"chunk_id": "drop", "quality_score": 0.0, "score": 0.95},
            {"chunk_id": "b", "quality_score": 0.5, "score": 0.7},
            {"chunk_id": "c", "quality_score": 0.5, "score": 0.6},
        ]
        out, _ = filter_quality_floor(chunks, floor=0.05)
        assert [c["chunk_id"] for c in out] == ["a", "b", "c"]

    @pytest.mark.parametrize("invalid", [None, "0.0", "high"])
    def test_non_numeric_quality_score_treated_as_default(self, invalid: object) -> None:
        """Defensive: payload corruption (string instead of float) MUST NOT
        crash; treat as default 0.5 and let the chunk through. Bug at
        ingest deserves a separate alert, not a retrieval crash."""
        chunks = [{"chunk_id": "x", "quality_score": invalid, "score": 0.5}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert len(out) == 1
        assert n_filtered == 0


class TestFeedbackColdStartExemption:
    """SPEC-KB-015 REQ r.182: a single data point must not affect ranking.

    The scorer's running average ``(old * count + signal) / (count + 1)``
    yields exactly ``0.0`` on the first thumbsDown (r.94-96). Without an
    exemption the floor would then drop the chunk entirely — a far stronger
    effect than the +-10%% boost that KB-015 deliberately gates behind
    ``feedback_count >= 3``.

    ``feedback_count == 0`` is untouched: that is the auth-wall ``degrade``
    case this filter exists for (SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07).
    """

    def test_single_thumbs_down_is_not_filtered(self) -> None:
        chunks = [{"chunk_id": "one-vote", "quality_score": 0.0, "feedback_count": 1}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 0
        assert [c["chunk_id"] for c in out] == ["one-vote"]

    def test_two_votes_still_below_cold_start_is_not_filtered(self) -> None:
        chunks = [{"chunk_id": "two-votes", "quality_score": 0.0, "feedback_count": 2}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 0
        assert [c["chunk_id"] for c in out] == ["two-votes"]

    def test_established_negative_signal_is_filtered(self) -> None:
        chunks = [{"chunk_id": "three-votes", "quality_score": 0.0, "feedback_count": 3}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 1
        assert out == []

    def test_auth_wall_degrade_chunk_still_filtered(self) -> None:
        """feedback_count 0 = never voted on = the REQ-07 case. Unchanged."""
        chunks = [{"chunk_id": "walled", "quality_score": 0.0, "feedback_count": 0}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 1
        assert out == []

    @pytest.mark.parametrize("invalid", ["1", None, [], {}])
    def test_non_numeric_feedback_count_treated_as_zero(self, invalid: object) -> None:
        """A corrupt count must not become an accidental exemption."""
        chunks = [{"chunk_id": "corrupt", "quality_score": 0.0, "feedback_count": invalid}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.05)
        assert n_filtered == 1
        assert out == []

    def test_exemption_does_not_rescue_a_high_floor_deployment(self) -> None:
        """The exemption is scoped to cold-start feedback, not to floors at large.

        An operator-raised floor (e.g. 0.6 to surface only boosted chunks) is a
        recall decision, not a feedback signal — cold-start chunks sit below it
        legitimately.
        """
        chunks = [{"chunk_id": "mid", "quality_score": 0.5, "feedback_count": 1}]
        out, n_filtered = filter_quality_floor(chunks, floor=0.6)
        assert n_filtered == 1
        assert out == []


class TestColdStartThresholdIsShared:
    """The floor and the boost must agree on what counts as a signal.

    Both modules define _COLD_START_MIN_VOTES and a comment in each says they
    have to stay equal. A comment is not a guard, and "two files, two
    assumptions" is exactly the shape of the defect this exemption fixes: the
    floor and the scorer each held a defensible view of what 0.0 meant, and
    nothing forced them to meet.

    Raise the boost's threshold without raising the floor's and chunks become
    filterable while still too cold to be boosted -- the original bug, back.
    Lower it in one place only and a single vote regains the power to remove a
    chunk. Either direction, this test fails first.
    """

    def test_floor_and_boost_use_the_same_threshold(self) -> None:
        assert quality_floor._COLD_START_MIN_VOTES == quality_boost._COLD_START_MIN_VOTES, (
            "quality_floor and quality_boost disagree about the cold-start "
            f"threshold ({quality_floor._COLD_START_MIN_VOTES} vs "
            f"{quality_boost._COLD_START_MIN_VOTES}). SPEC-KB-015 r.182 defines one "
            "guard, not two. Change both or neither."
        )

    def test_threshold_matches_the_spec(self) -> None:
        """SPEC-KB-015 r.112/118 fixes the value at 3 votes."""
        assert quality_floor._COLD_START_MIN_VOTES == 3


class TestFloorPerformance:
    def test_p99_under_2ms_on_1k_chunks(self) -> None:
        """Floor filter is on the hot path; budget is < 2ms p99 on 1000
        chunks (a generous over-estimate of real top_k)."""
        import time

        chunks = [{"chunk_id": f"c-{i}", "quality_score": 0.5, "score": 0.5} for i in range(1000)]
        chunks[7]["quality_score"] = 0.0
        chunks[123]["quality_score"] = 0.0

        timings_ms = []
        for _ in range(500):
            t0 = time.perf_counter()
            filter_quality_floor(chunks, floor=0.05)
            timings_ms.append((time.perf_counter() - t0) * 1000)

        timings_ms.sort()
        p99 = timings_ms[int(len(timings_ms) * 0.99)]
        assert p99 < 2.0, f"p99 {p99:.3f}ms exceeds 2ms budget"
