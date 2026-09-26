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

import json

import httpx
from klai_llm_throttle import TokenBucketLimiter

from knowledge_ingest.config import settings

__all__ = [
    "NoMediumFallbackTransport",
    "TaggingTransport",
    "TokenBucketLimiter",
    "add_no_fallback",
    "shared_klai_fast_limiter",
    "with_feature_tag",
]

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


# ---------------------------------------------------------------------------
# No-medium-fallback (measured September 2026: $583 of ~$1,048 LLM spend was
# background graph/enrichment/taxonomy work silently served by klai-medium at
# ~10-12.5x klai-fast's price, because deploy/litellm/config.yaml's
# router_settings.fallbacks routes a saturated klai-fast to klai-medium —
# a fallback meant for interactive chat latency, not background work).
#
# ``fallbacks: []`` per request, NOT ``disable_fallbacks: true``. Verified
# against the litellm==1.96.2 actually running on core-01
# (klai-core-litellm-1), by reading the installed source and by a live probe
# through klai-portal/backend's settings.litellm_base_url:
#
#   * ``disable_fallbacks: true`` short-circuits
#     ``Router.async_function_with_fallbacks_common_utils`` before it builds
#     ANY fallback list -- which also skips the "ORDER-BASED FALLBACKS" block
#     that fails ``klai-fast``'s order:1 (subscription) deployment over to
#     order:2 (PAYG) on the SAME model. Live proof: with the subscription key
#     currently over its Mistral workspace monthly spending limit,
#     ``disable_fallbacks: true`` returned the raw 402 from Mistral instead of
#     silently succeeding via order:2 the way a plain request does.
#   * ``fallbacks: []`` (a per-request override of the model-group fallback
#     list) is read independently of the order-based block -- order-based
#     failover still runs, only the escalation to klai-medium is suppressed.
#     Live-verified: a request with ``fallbacks: []`` against the
#     currently-degraded order:1 key still returned 200 via order:2's
#     mistral-small-2603, exactly like a request with no override at all.
def with_feature_tag(payload: dict, *, tag: str) -> dict:
    """Return ``payload`` with ``tag`` recorded as a LiteLLM spend tag.

    Written to ``metadata.tags`` -- the field ``LiteLLM_SpendLogs.request_tags``
    is read from (``get_logging_payload`` in litellm's
    ``spend_tracking_utils.py``), verified against the litellm 1.96.2 actually
    running on core-01 (klai-core-litellm-1), by reading the installed source.
    That same payload builder runs on both the success and the failure logging
    path, so a tag survives a 402/429 row exactly like a 200 one. ``tag`` is
    required (not defaulted) so a new caller cannot forget to name its feature.
    """
    metadata = payload.get("metadata") or {}
    return {**payload, "metadata": {**metadata, "tags": [*(metadata.get("tags") or []), tag]}}


# ---------------------------------------------------------------------------
# No-medium-fallback (measured September 2026: $583 of ~$1,048 LLM spend was
# background graph/enrichment/taxonomy work silently served by klai-medium at
# ~10-12.5x klai-fast's price, because deploy/litellm/config.yaml's
# router_settings.fallbacks routes a saturated klai-fast to klai-medium —
# a fallback meant for interactive chat latency, not background work).
#
# ``fallbacks: []`` per request, NOT ``disable_fallbacks: true``. Verified
# against the litellm==1.96.2 actually running on core-01
# (klai-core-litellm-1), by reading the installed source and by a live probe
# through klai-portal/backend's settings.litellm_base_url:
#
#   * ``disable_fallbacks: true`` short-circuits
#     ``Router.async_function_with_fallbacks_common_utils`` before it builds
#     ANY fallback list -- which also skips the "ORDER-BASED FALLBACKS" block
#     that fails ``klai-fast``'s order:1 (subscription) deployment over to
#     order:2 (PAYG) on the SAME model. Live proof: with the subscription key
#     currently over its Mistral workspace monthly spending limit,
#     ``disable_fallbacks: true`` returned the raw 402 from Mistral instead of
#     silently succeeding via order:2 the way a plain request does.
#   * ``fallbacks: []`` (a per-request override of the model-group fallback
#     list) is read independently of the order-based block -- order-based
#     failover still runs, only the escalation to klai-medium is suppressed.
#     Live-verified: a request with ``fallbacks: []`` against the
#     currently-degraded order:1 key still returned 200 via order:2's
#     mistral-small-2603, exactly like a request with no override at all.
def add_no_fallback(payload: dict, *, tag: str) -> dict:
    """Return a chat-completions payload with ``fallbacks: []`` set, tagged with ``tag``.

    Use for every klai-fast call made by background ingest work (graph
    extraction, enrichment, taxonomy, labeling, selector-AI, RAG eval). Chat
    traffic (portal-api / LibreChat's klai-primary and klai-fast) must keep
    the global fallback, so this is applied per-request here, not in
    ``deploy/litellm/config.yaml``.
    """
    return {**with_feature_tag(payload, tag=tag), "fallbacks": []}


def _rewrite_body(request: httpx.Request, transform) -> httpx.Request:
    """Rebuild a POST request with ``transform`` applied to its JSON body.

    A GET, or a body without a ``messages`` or ``input`` key (neither a
    chat-completions nor an embeddings call), passes through untouched.
    """
    if request.method != "POST":
        return request
    try:
        body = json.loads(request.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return request
    if not isinstance(body, dict) or ("messages" not in body and "input" not in body):
        return request
    headers = httpx.Headers(
        [(k, v) for k, v in request.headers.raw if k.lower() != b"content-length"]
    )
    return httpx.Request(
        method=request.method,
        url=request.url,
        headers=headers,
        json=transform(body),
        # httpcore reads the client's timeouts from here; a rebuilt request
        # without them has no timeout at all.
        extensions=request.extensions,
    )


class NoMediumFallbackTransport(httpx.AsyncBaseTransport):
    """Wraps a transport, adding ``fallbacks: []`` and a spend tag to every JSON POST body.

    For callers that hand LiteLLM traffic to a library that builds its own
    request body (Graphiti's ``AsyncOpenAI`` client, RAGAS' judge LLM) rather
    than constructing the payload dict themselves -- see ``add_no_fallback``
    for callers that do.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport, *, tag: str) -> None:
        self._wrapped = wrapped
        self._tag = tag

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request = _rewrite_body(request, lambda body: add_no_fallback(body, tag=self._tag))
        return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()


class TaggingTransport(httpx.AsyncBaseTransport):
    """Wraps a transport, adding only a spend tag -- no ``fallbacks: []``.

    For a call that already targets its model directly and has no fallback
    entry to suppress (RAGAS' faithfulness ``heavy_llm``, pinned to
    klai-medium), so tagging must not imply a fallback opinion it does not
    need.
    """

    def __init__(self, wrapped: httpx.AsyncBaseTransport, *, tag: str) -> None:
        self._wrapped = wrapped
        self._tag = tag

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request = _rewrite_body(request, lambda body: with_feature_tag(body, tag=self._tag))
        return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()
