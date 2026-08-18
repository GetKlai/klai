# klai-llm-throttle

Shared async token-bucket rate limiter for every direct or proxied LLM call
Klai makes against a shared upstream budget (e.g. the `klai-fast` alias's
45 rpm slice of Mistral's 100 rpm capacity).

## Why this exists

Klai has had this exact incident twice in two different services because the
limiter lived inside one service and a second, independent caller elsewhere
had no way to see or respect it:

1. **knowledge-ingest** (2026-08-14): bulk enrichment fired unthrottled
   `klai-fast` calls; Graphiti paced only its own calls. Combined they blew
   through the 45 rpm alias budget — 641 `enrichment_llm_error` events in two
   weeks. Fixed with a process-local shared token bucket
   (`shared_klai_fast_limiter`).
2. **deploy/litellm query-rewrite hook** (2026-08-18): the pasted-correspondence
   distillation feature (SPEC-RAG-CORRESPONDENCE-DISTILL-001) calls Mistral
   directly (`https://api.mistral.ai/v1/chat/completions`), bypassing the
   litellm proxy's own rpm accounting entirely. This traffic was invisible to
   knowledge-ingest's limiter (a different process, a different package) and
   to litellm's own `klai-fast`/`klai-primary` router accounting. Result:
   1000+ `RouterRateLimitError`/429 events in a single hour of real
   production chat traffic.

Both were the same bug: **a second, uncoordinated caller of a shared budget.**
Fixing it a second time in a third bespoke copy would just create a third
uncoordinated implementation. This package is the single, shared
implementation both services import — one class, no duplication, easy to
find the next time a third caller needs it.

## What this package does NOT do

This is a **process-local** limiter. It does not coordinate across separate
OS processes/containers (that would need a Redis-backed distributed limiter —
a bigger lift, not justified while every current caller of a shared budget
lives in a single process per service). Each service constructs its own
`TokenBucketLimiter` instance, sized so that its own worst-case traffic stays
under its slice of the real upstream budget with headroom for the other
services sharing that same upstream capacity.

## Usage

```python
from klai_llm_throttle import TokenBucketLimiter

# Module-level lazy singleton, one per process per budget.
_limiter: TokenBucketLimiter | None = None

def shared_limiter() -> TokenBucketLimiter:
    global _limiter
    if _limiter is None:
        _limiter = TokenBucketLimiter(rate=0.6, capacity=10)
    return _limiter

await shared_limiter().acquire()
resp = await client.post(url, ...)
```
