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

``TokenBucketLimiter`` itself lives in the shared ``klai_llm_throttle``
package (klai-libs/llm-throttle), not here. The former direct-Mistral
LiteLLM consumer now routes through the proxy and no longer imports this
package, leaving knowledge-ingest as its only runtime consumer. This module
owns the knowledge-ingest-specific singleton wiring (its own settings and
lazy-init getter).
"""

from __future__ import annotations

from klai_llm_throttle import TokenBucketLimiter

from knowledge_ingest.config import settings

__all__ = ["TokenBucketLimiter", "shared_klai_fast_limiter"]

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
