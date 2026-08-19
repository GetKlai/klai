# klai-llm-throttle

Shared async token-bucket rate limiter for direct LLM calls that cannot use
LiteLLM's centrally enforced proxy quotas.

## Why this exists

Klai introduced this package after knowledge-ingest bulk enrichment fired
unthrottled direct `klai-fast` calls while Graphiti paced only its own calls.
Combined they exceeded the service's upstream budget. The process-local shared
bucket coordinates those direct calls inside knowledge-ingest.

The LiteLLM query-rewrite hook deliberately does **not** use this package. It
calls the local LiteLLM proxy with the `klai-fast` alias, so the proxy's RPM,
TPM, retry and fallback policy applies to rewrite traffic together with all
other proxied calls. Adding another process-local limiter there would recreate
two independent views of one upstream budget.

## What this package does NOT do

This is a **process-local** limiter. It does not coordinate across separate OS
processes or containers and it does not count prompt/completion tokens. Use it
only when a workload must call a provider directly; prefer LiteLLM's central
proxy quotas for proxied traffic.

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
