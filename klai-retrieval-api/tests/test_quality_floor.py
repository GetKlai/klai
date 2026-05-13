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
