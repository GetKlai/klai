"""Tests for knowledge_ingest.llm_throttle -- shared klai-fast token bucket.

See knowledge_ingest/llm_throttle.py module docstring for the incident this
module fixes (641 enrichment_llm_error 429s in two weeks, pre-2026-08-14).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from knowledge_ingest.llm_throttle import (
    NoMediumFallbackTransport,
    TokenBucketLimiter,
    add_no_fallback,
    shared_klai_fast_limiter,
)


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


class TestNoMediumFallbackContract:
    """Regression for the September 2026 klai-medium-escalation incident.

    $583 of ~$1,048 LLM spend that month was background graph/enrichment/
    taxonomy work silently served by klai-medium (~10-12.5x klai-fast's
    price) because a saturated klai-fast fell back to klai-medium under
    deploy/litellm/config.yaml's router_settings.fallbacks -- a fallback
    meant for interactive chat latency, not background work.
    """

    def test_add_no_fallback_blocks_medium_without_touching_the_rest(self):
        payload = {"model": "klai-fast", "messages": [{"role": "user", "content": "hi"}]}

        result = add_no_fallback(payload)

        assert result["fallbacks"] == []
        assert result["model"] == "klai-fast"
        assert result["messages"] == payload["messages"]
        # Must not mutate the caller's dict -- callers may reuse it.
        assert "fallbacks" not in payload

    @pytest.mark.asyncio
    async def test_no_medium_fallback_transport_rewrites_outbound_request(self):
        """Graphiti's shared AsyncOpenAI client (graph.py) relies on this
        transport, since it never builds its own request dict -- this is the
        one central place that must carry the no-fallback instruction for
        every call graphiti-core makes internally."""
        captured: list[httpx.Request] = []

        class _CapturingTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
                captured.append(request)
                return httpx.Response(200, json={"ok": True}, request=request)

        transport = NoMediumFallbackTransport(_CapturingTransport())
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(7.0)) as client:
            await client.post(
                "http://litellm.internal/v1/chat/completions",
                headers={"Authorization": "Bearer secret-key"},
                json={"model": "klai-fast", "messages": [{"role": "user", "content": "hi"}]},
            )

        assert len(captured) == 1
        sent_body = json.loads(captured[0].content)
        assert sent_body["fallbacks"] == []
        assert sent_body["model"] == "klai-fast"
        # Auth must survive the request being rebuilt.
        assert captured[0].headers["authorization"] == "Bearer secret-key"
        # So must the client's timeouts: httpcore reads them from the request
        # extensions, and without them a stuck call would hang forever.
        assert captured[0].extensions["timeout"]["read"] == 7.0

    def test_every_chat_completions_caller_blocks_medium_fallback(self):
        """Every knowledge-ingest module that POSTs a raw chat/completions
        body for background work must route it through add_no_fallback --
        mirrors TestChatCompletionsThrottleDriftGuard's pattern for the rate
        limiter above."""
        package_root = Path(__file__).resolve().parent.parent / "knowledge_ingest"
        offenders: list[str] = []

        for path in sorted(package_root.rglob("*.py")):
            relative = path.relative_to(package_root)
            if "tests" in relative.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "chat/completions" in source and "add_no_fallback" not in source:
                offenders.append(str(relative))

        assert not offenders, (
            "These files POST to chat/completions without routing the payload "
            f"through add_no_fallback() -- a saturated klai-fast would silently "
            f"escalate to klai-medium: {offenders}"
        )
