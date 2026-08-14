"""Shared client-side rate limiter for every LiteLLM ``klai-fast`` call.

Why this exists
---------------

The ``klai-fast`` alias in ``deploy/litellm/config.yaml`` has a 45 rpm / 45k
tpm budget (half of mistral-small's upstream 100 rpm, shared with
``klai-primary``). Before 2026-08-14, knowledge-ingest offered LiteLLM far
more than that during bulk crawls: the LLM worker-lane ran 4 concurrent
enrichment jobs firing one unthrottled chat call per chunk, while Graphiti
paced only its OWN calls (0.5 rps ≈ 30 rpm — already most of the alias
budget on its own). The overflow bounced as 429s: 641 ``enrichment_llm_error``
events in two weeks, 1165 permanently-failed enrich-bulk jobs, and the
intermedia.com source that stayed broken for 8 days.

A Procrastinate queue orders the *jobs* but does not pace the *HTTP calls*
the job bodies make. This module is that missing pacing layer: one
process-wide token bucket that every ``klai-fast`` chat call acquires from,
tuned under the alias budget so sustained bulk work simply runs slower
instead of erroring.

Burst capacity keeps interactive work snappy: an idle bucket lets the first
``litellm_klai_fast_burst`` calls through immediately (a single-document
re-sync mostly fits in the burst), and only sustained load degrades to the
refill rate.

Usage::

    from knowledge_ingest.llm_throttle import shared_klai_fast_limiter

    await shared_klai_fast_limiter().acquire()
    resp = await client.post(f"{settings.litellm_url}/v1/chat/completions", ...)

``tests/test_llm_throttle.py`` contains a drift-guard that fails when a
module POSTs to ``chat/completions`` without referencing this limiter.
"""

from __future__ import annotations

import asyncio

from knowledge_ingest.config import settings


class TokenBucketLimiter:
    """Async token bucket: ``rate`` sustained calls/sec with ``capacity`` burst.

    ``acquire()`` returns immediately while burst tokens remain and sleeps
    just long enough for the next token otherwise. The sleep happens while
    holding the internal lock, so concurrent acquirers are served strictly
    in arrival order — same serialisation behaviour as the previous
    graph.py ``_TokenBucketLimiter``, now with a burst allowance.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate}")
        self._rate = rate
        self._capacity = max(1.0, capacity if capacity is not None else rate)
        self._tokens = self._capacity
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            if self._last is not None:
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self._rate
            self._tokens = 0.0
            await asyncio.sleep(wait)
            self._last = loop.time()


_shared_limiter: TokenBucketLimiter | None = None


def shared_klai_fast_limiter() -> TokenBucketLimiter:
    """Process-wide limiter for ALL klai-fast LiteLLM calls (lazy singleton).

    Shared across enrichment, Graphiti, taxonomy, selector-AI, labelers and
    the RAGAS judge so their combined rate stays under the 45 rpm alias
    budget. Default 0.6 rps = 36 rpm, leaving headroom for retries.
    """
    global _shared_limiter
    if _shared_limiter is None:
        _shared_limiter = TokenBucketLimiter(
            rate=settings.litellm_klai_fast_rps,
            capacity=settings.litellm_klai_fast_burst,
        )
    return _shared_limiter
