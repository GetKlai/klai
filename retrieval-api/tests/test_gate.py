"""Tests for retrieval gate service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.services import gate


@pytest.fixture(autouse=True)
def reset_gate_cache():
    """Reset module-level caches before each test."""
    gate.reset_cache()
    yield
    gate.reset_cache()


class TestGate:
    @pytest.mark.asyncio
    async def test_gate_disabled_returns_false(self):
        """When gate is disabled, always returns (False, None)."""
        with patch.object(gate.settings, "retrieval_gate_enabled", False):
            bypass, margin = await gate.should_bypass([0.1, 0.2, 0.3])
            assert bypass is False
            assert margin is None

    @pytest.mark.asyncio
    async def test_no_reference_file_returns_false(self):
        """When no gate_reference.jsonl exists, returns (False, None)."""
        with patch.object(gate, "_GATE_FILE", gate.Path("/nonexistent/file.jsonl")):
            bypass, margin = await gate.should_bypass([0.1, 0.2, 0.3])
            assert bypass is False
            assert margin is None

    @pytest.mark.asyncio
    async def test_margin_above_threshold_bypasses(self):
        """When margin > threshold, bypass is True."""
        # Simulate cached reference vectors where top-1 is very close
        # and top-2 is distant (large margin)
        ref_vectors = [
            [1.0, 0.0, 0.0],  # Very similar to query
            [0.0, 1.0, 0.0],  # Orthogonal to query
        ]

        gate._reference_queries = [
            {"query": "q1", "label": "A"},
            {"query": "q2", "label": "B"},
        ]
        gate._reference_vectors = ref_vectors

        with patch.object(gate.settings, "retrieval_gate_threshold", 0.1):
            with patch.object(gate.settings, "retrieval_gate_enabled", True):
                query_vector = [0.99, 0.01, 0.0]  # close to ref_vectors[0]
                bypass, margin = await gate.should_bypass(query_vector)
                assert bypass is True
                assert margin is not None
                assert margin > 0.1

    @pytest.mark.asyncio
    async def test_margin_below_threshold_does_not_bypass(self):
        """When margin <= threshold, bypass is False."""
        # Two reference vectors equidistant from query
        ref_vectors = [
            [0.7, 0.7, 0.0],
            [0.7, 0.0, 0.7],
        ]

        gate._reference_queries = [
            {"query": "q1", "label": "A"},
            {"query": "q2", "label": "B"},
        ]
        gate._reference_vectors = ref_vectors

        with patch.object(gate.settings, "retrieval_gate_threshold", 0.5):
            with patch.object(gate.settings, "retrieval_gate_enabled", True):
                query_vector = [0.7, 0.35, 0.35]
                bypass, margin = await gate.should_bypass(query_vector)
                assert bypass is False
                assert margin is not None

    @pytest.mark.asyncio
    async def test_tei_failure_returns_false(self):
        """When TEI embedding fails, returns (False, None)."""
        gate._reference_queries = [{"query": "test", "label": "A"}]
        # Force re-embedding by not setting _reference_vectors
        gate._reference_vectors = None

        with patch(
            "retrieval_api.services.tei.embed_batch",
            new_callable=AsyncMock,
            side_effect=Exception("TEI down"),
        ):
            with patch.object(gate.settings, "retrieval_gate_enabled", True):
                bypass, margin = await gate.should_bypass([0.1, 0.2])
                assert bypass is False
                assert margin is None
