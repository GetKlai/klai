"""Tests for knowledge_ingest.llm_throttle -- shared klai-fast token bucket.

See knowledge_ingest/llm_throttle.py module docstring for the incident this
module fixes (641 enrichment_llm_error 429s in two weeks, pre-2026-08-14).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from knowledge_ingest.llm_throttle import TokenBucketLimiter, shared_klai_fast_limiter


class TestTokenBucketLimiterBurst:
    @pytest.mark.asyncio
    async def test_burst_capacity_returns_near_instantly(self):
        """Acquires within the burst capacity must not sleep meaningfully."""
        limiter = TokenBucketLimiter(rate=100, capacity=3)

        t0 = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        elapsed = time.monotonic() - t0

        assert elapsed < 0.1


class TestTokenBucketLimiterPacing:
    @pytest.mark.asyncio
    async def test_exhausted_burst_paces_to_rate(self):
        """Once the single-token burst is spent, subsequent acquires pace at ``rate``."""
        limiter = TokenBucketLimiter(rate=50, capacity=1)

        t0 = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - t0

        # First acquire is free (burst=1); the remaining 4 pace at 1/50s each,
        # so the floor is 4/50 = 0.08s. Assert a slightly looser floor to
        # absorb scheduler jitter while still catching a broken/no-op limiter.
        assert elapsed >= 0.06
        # Keep the test fast — must stay well under a second.
        assert elapsed < 1.0


class TestSharedKlaiFastLimiterSingleton:
    def test_returns_same_instance_across_calls(self):
        first = shared_klai_fast_limiter()
        second = shared_klai_fast_limiter()

        assert first is second


class TestChatCompletionsThrottleDriftGuard:
    """Fails loudly when a new klai-fast caller forgets to throttle.

    Every module under ``knowledge_ingest/`` (excluding tests) that POSTs to
    a ``chat/completions`` endpoint MUST also reference
    ``shared_klai_fast_limiter`` -- otherwise it bypasses the shared budget
    and can trigger 429s on LiteLLM again, exactly like the incident this
    module was built to fix.
    """

    def test_every_chat_completions_caller_uses_shared_limiter(self):
        package_root = Path(__file__).resolve().parent.parent / "knowledge_ingest"
        offenders: list[str] = []

        for path in sorted(package_root.rglob("*.py")):
            relative = path.relative_to(package_root)
            if "tests" in relative.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "chat/completions" in source and "shared_klai_fast_limiter" not in source:
                offenders.append(str(relative))

        assert not offenders, (
            "These files POST to chat/completions without acquiring from "
            "shared_klai_fast_limiter() -- they bypass the shared klai-fast "
            f"rate budget: {offenders}"
        )
