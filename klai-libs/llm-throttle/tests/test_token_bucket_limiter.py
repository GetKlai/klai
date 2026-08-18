"""Tests for klai_llm_throttle.TokenBucketLimiter.

Extracted from knowledge_ingest/llm_throttle.py (2026-08-14 incident fix)
into a shared package after the SAME class of bug recurred in a second,
independent service (deploy/litellm/klai_kb_query_rewrite.py, 2026-08-18) —
a direct-to-Mistral caller with no shared rate accounting, invisible to the
first fix because it lived in a different process/package entirely. See the
package README for the full incident history.
"""

from __future__ import annotations

import time

import pytest

from klai_llm_throttle import TokenBucketLimiter


class TestConstruction:
    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError, match="rate must be > 0"):
            TokenBucketLimiter(rate=0)

    def test_rejects_negative_rate(self):
        with pytest.raises(ValueError, match="rate must be > 0"):
            TokenBucketLimiter(rate=-1)

    def test_capacity_defaults_to_rate(self):
        limiter = TokenBucketLimiter(rate=5)
        assert limiter._capacity == 5.0

    def test_capacity_floors_at_one(self):
        limiter = TokenBucketLimiter(rate=0.1, capacity=0.1)
        assert limiter._capacity == 1.0


class TestBurst:
    @pytest.mark.asyncio
    async def test_burst_capacity_returns_near_instantly(self):
        limiter = TokenBucketLimiter(rate=100, capacity=3)

        t0 = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.monotonic() - t0

        assert elapsed < 0.1


class TestPacing:
    @pytest.mark.asyncio
    async def test_exhausted_burst_paces_to_rate(self):
        limiter = TokenBucketLimiter(rate=50, capacity=1)

        t0 = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - t0

        # First acquire is free (burst=1); the remaining 4 pace at 1/50s each,
        # so the floor is 4/50 = 0.08s.
        assert elapsed >= 0.06
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_concurrent_acquires_serialize_in_arrival_order(self):
        """Concurrent callers must not exceed the configured rate collectively."""
        import asyncio

        limiter = TokenBucketLimiter(rate=20, capacity=1)
        t0 = time.monotonic()
        await asyncio.gather(*(limiter.acquire() for _ in range(6)))
        elapsed = time.monotonic() - t0

        # 1 free (burst) + 5 paced at 1/20s = 0.25s floor.
        assert elapsed >= 0.2
