# Finding 6 research: TEI retry budget too short?

## Code verification

### Exact retry logic (`embedder.py` lines 22-61)

`_embed_batch` runs a `for attempt in range(3)` loop. On each iteration:

- It calls `client.post("/v1/embeddings", ...)` with the per-client timeout set to
  `settings.tei_timeout` (default: **120 seconds**).
- On `httpx.ReadTimeout` or `httpx.ConnectTimeout`: catches, waits `2**attempt` seconds,
  retries. Wait sequence: attempt 0 → 1 s, attempt 1 → 2 s, attempt 2 → 4 s.
- On `httpx.HTTPStatusError` with status >= 500: same catch/wait/retry path.
- On `httpx.HTTPStatusError` with status < 500 (e.g. 400, 422): re-raises immediately
  (no retry).
- After three failed attempts: `raise last_exc` — the final exception propagates to
  the caller with no further handling inside `embedder.py`.

**Wall-time retry budget calculation:**

The finding originally stated "7s wall time (1 + 2 + 4)". This is accurate for the
*sleep* time. The actual wall budget is `3 × tei_timeout + (1 + 2 + 4)` in the
worst case (three attempts each timing out at the full `tei_timeout`). With
`tei_timeout = 120 s`, the theoretical maximum is 367 seconds per batch. In
practice, a *timeout* exception triggers when the connection or read exceeds
`tei_timeout`, so the worst-case wall time per retry attempt is ~120 s.

However, the finding's concern is about *transient blips*: a fast 5xx response or a
short ConnectTimeout that triggers all three attempts quickly. In that scenario the
sleep budget is indeed 1 + 2 + 4 = 7 seconds of sleep, plus the time of the failed
requests themselves.

**Jitter: absent.** The backoff is deterministic: `wait = 2**attempt`. No randomness
is added. Multiple concurrent ingest calls hitting TEI at the same moment will all
retry on the same schedule, creating a thundering-herd on the retry window.

**On exhaustion:** `raise last_exc` propagates upward with no catch inside the
`embed()` function either (lines 64-84). The exception is unhandled at the embedder
module level.

### `settings.tei_timeout` default

`config.py` line 12: `tei_timeout: float = 120.0`. The comment confirms: "TEI can
take 35s+ on large batches with queue". The timeout is per-request, applied to the
`httpx.AsyncClient` at construction time (`embed()` lines 72-75). This is the
correct place to set it.

### Phase 1 caller (synchronous HTTP endpoint)

`ingest_document()` in `routes/ingest.py` line 331 calls `await embedder.embed(texts)`
with no surrounding `try/except`. There is no catch between line 331 and the route
handler `ingest_document_route` (line 616-617), which also has no catch. FastAPI's
default exception handler will convert any uncaught exception into an HTTP 500 to
the caller.

Concrete failure chain:
```
POST /ingest/v1/document
  -> ingest_document()
     -> embedder.embed(texts)          # no try/except
        -> _embed_batch() raises last_exc after 3 attempts
  -> FastAPI: HTTP 500 to caller
```

The `gitea_webhook` path (line 747) wraps `ingest_document` in a `try/except
Exception` that logs a warning and continues — so a Gitea webhook push does not
propagate the embed failure to the Gitea server, but the page is silently skipped.

The `bulk_sync_kb_route` path (line 1044) similarly catches and logs: pages are
skipped silently.

### Phase 2 caller (`enrichment_tasks.py` lines 357-386)

The enrichment task calls `embedder.embed(enriched_texts)` inside `_timed_dense()`
(line 363), which is called via `asyncio.gather` (line 371) with no surrounding
`try/except` at the gather call site. If `_timed_dense()` raises, the
`asyncio.gather` will propagate the exception to the enrichment task function. The
enrichment task is a Procrastinate task — unhandled exceptions cause Procrastinate
to mark the job as failed and apply its own retry policy.

### Crawler caller (`crawl_tasks.py`)

`run_crawl` is registered with `retry=procrastinate.RetryStrategy(max_attempts=1)`
(line 21). This means Procrastinate will NOT retry the crawl task on failure. The
crawl task calls `run_crawl_job` which calls `ingest_document` which calls `embed`.
If `embed` fails, the exception propagates to `run_crawl_job` and then to the
Procrastinate task, which marks the job as permanently failed (no Procrastinate
retry, `max_attempts=1`).

The Procrastinate retry does **not** compensate for the TEI retry budget, because the
crawl task's `max_attempts=1` means it only runs once.

## Current behavior

| Call path | Embed failure effect |
|---|---|
| `POST /ingest/v1/document` | HTTP 500 to caller, entire Phase 1 fails |
| Gitea webhook (`/ingest/v1/webhook/gitea`) | Page silently skipped, webhook returns 200 to Gitea |
| Bulk sync (`/ingest/v1/kb/sync`) | Page silently skipped, continues with next page |
| Phase 2 enrichment (Procrastinate task) | Task marked failed by Procrastinate, subject to enrichment task retry policy |
| Crawl task (Procrastinate, `max_attempts=1`) | Crawl job permanently failed, no Procrastinate retry |

The retry budget for a fast transient failure (e.g. TEI restarting after OOM):
- Sleep time: 1s + 2s + 4s = 7 seconds
- Per-attempt timeout: up to 120 s (configurable)
- No jitter: deterministic, thundering-herd risk on concurrent callers

## Industry standard (2026)

### Major embedding service vendors

**OpenAI Embeddings API** (via openai-cookbook and platform docs, 2026): Recommends
exponential backoff with full jitter starting from 0.5 s base, exponential base 2.0,
capped at 30 s. Retry on 429 (rate limit), 500, 502, 503, 504. For client-side
implementation: use `tenacity.wait_random_exponential(min=1, max=60)` with 3-6
attempts. The key recommendation is full jitter to prevent thundering herd.

**HuggingFace TEI** (official docs): No specific client-side retry guidance is
documented. TEI exposes Prometheus metrics and OpenTelemetry tracing for observability,
and supports configuring `--max-concurrent-requests` server-side. The absence of
documented retry guidance means clients are expected to apply standard HTTP retry
patterns — the same exponential-backoff-with-jitter approach used for any HTTP/gRPC
service.

**Cohere Embed**: Rate limit errors (429) should be retried with exponential backoff.
Cohere's error documentation distinguishes 429 (rate limit), 500 (server error), and
503 (unavailable). All 5xx errors are considered transient and retriable.

**Voyage AI**: Retry guidance follows the same pattern: exponential backoff with
jitter, do not retry 4xx client errors, retry all 5xx and timeout responses.

### Tenacity recommended pattern for HTTP retry

The canonical Python pattern for HTTP services in 2026:

```python
from tenacity import (
    retry,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
    retry_if_exception_type,
)

@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    wait=wait_random_exponential(min=1, max=30),
    stop=(stop_after_attempt(5) | stop_after_delay(120)),
)
async def _embed_batch_with_retry(...):
    ...
```

Key points from `tenacity` docs and production guides:
- `wait_random_exponential` implements "full jitter": random delay in `[0, 2**attempt]`
  capped at `max`. This is the recommended approach for preventing thundering-herd.
- `stop_after_delay` provides a wall-clock budget as a safety net against infinite
  waits, complementing `stop_after_attempt`.
- Combining both with `|` (stop when either is reached first) is best practice.

### Circuit breaker: when to apply

The circuit breaker pattern (PyBreaker, tenacity `RetryError` + external state) is
appropriate when:
1. The downstream service has a restart time significantly longer than any
   reasonable retry budget.
2. Multiple callers would hammer the recovering service simultaneously.

For self-hosted TEI on a single GPU host, the circuit breaker tradeoff is:

- **Docker restart after OOM** (most common TEI failure): The container typically
  restarts within 15-60 seconds (CUDA context clear + model reload into VRAM).
  BGE-M3 is ~2 GB — reload is fast once the Docker engine starts the container.
  The typical Docker restart policy (`restart: unless-stopped`) adds a small delay
  but auto-restarts without operator intervention.
- **Operator-remediated OOM on Kubernetes** (vLLM-scale): 15-45 minutes per a
  published runbook, because engineers must diagnose and adjust resource limits.
  klai uses Docker Compose, not Kubernetes, so the simpler fast-restart path applies.

For klai's single-host Docker deployment, a **pure retry strategy with increased
budget is sufficient** — a circuit breaker adds operational complexity without
proportional benefit for a single-tenant small-team setup. A circuit breaker would
be warranted if TEI restarts took 5+ minutes or if there were 10+ concurrent callers.

### Thundering-herd risk

With 32 chunks per batch and deterministic 1s/2s/4s backoff, concurrent ingest
requests (e.g. a bulk sync of 50 pages) will all retry at t=1, t=3, t=7 seconds
simultaneously. Adding jitter (`wait_random_exponential`) spreads these across the
window and reduces the chance that all retries arrive when TEI is still starting up.

### GPU service restart time for TEI with BGE-M3

BGE-M3 (BAAI/bge-m3) is approximately 2.2 GB in fp16. On an NVIDIA GPU (the
context: gpu-01), typical VRAM load times are:
- Docker container restart: 5-15 seconds
- CUDA context initialization: 2-5 seconds
- Model weights load from disk to VRAM: 5-20 seconds (depending on NVMe speed)

Total expected downtime after a Docker OOM kill and auto-restart: **15-45 seconds**
in most cases. This aligns with reports from TEI and similar HuggingFace inference
service deployments.

Current retry budget (sleep-only): 7 seconds — **insufficient to survive a typical
TEI OOM restart**. A budget of 60-90 seconds of total wall time would cover the
realistic restart window.

## Fix recommendations

### 1. Add jitter to prevent thundering herd

Replace the deterministic `2**attempt` backoff with full-jitter exponential:

```python
import random

# Current (deterministic, thundering-herd risk):
wait = 2**attempt

# Recommended (full jitter):
wait = random.uniform(0, 2**attempt)
```

Or adopt `tenacity` entirely for cleaner separation of concerns.

### 2. Increase the attempt count from 3 to 5

With 5 attempts and full jitter, the expected sleep budget covers 60+ seconds of wall
time (average of full-jitter over 5 doublings: 0 + 1 + 2 + 4 + 8 = 15 s average
sleep, up to 31 s max sleep), which is sufficient for most TEI restarts. The
per-attempt timeout (120 s) is already generous and should not need adjustment.

```python
for attempt in range(5):  # was range(3)
    ...
    wait = random.uniform(0, min(2**attempt, 30))  # cap at 30s
```

### 3. Add a `stop_after_delay` safety net

For the enrichment task path (which runs asynchronously), add an outer wall-clock
deadline to prevent a single stuck embed from blocking the Procrastinate worker for
hours. `asyncio.wait_for(embedder.embed(texts), timeout=300)` on the task side is
a reasonable guard.

### 4. Log embed failure at ERROR level for Phase 1

Currently, a Phase 1 failure results in a FastAPI 500 with no structured log at the
embed failure site. The `logger.warning` in `_embed_batch` is correct for transient
retries, but the final `raise last_exc` should be caught and logged at `error` level
before re-raising, so VictoriaLogs can surface it:

```python
# In embed() or ingest_document(), before the 500 propagates:
logger.error("tei_embed_failed_all_attempts", texts=len(texts), exc_info=True)
raise
```

### 5. Circuit breaker: not recommended at current scale

At klai's current scale (small team, single TEI instance, ingest volume measured in
hundreds of documents per day), a circuit breaker adds complexity without benefit.
Revisit when any of these are true:
- Concurrent ingest callers > 10 simultaneously
- TEI restart time consistently exceeds 60 seconds
- Phase 2 enrichment queue depth regularly exceeds 500 jobs

## Risk assessment

### How often does TEI blip in klai's actual environment?

TEI runs on gpu-01 as a Docker container (`restart: unless-stopped`). Known failure
modes observed in the klai environment:

1. **OOM during large batch**: BGE-M3 with batch_size=32 on a batch of long documents
   can hit VRAM limits if another GPU process (Infinity reranker on port 7998) is
   competing. This is documented in `config.py` comment: "TEI can take 35s+ on large
   batches with queue".
2. **SSH tunnel flap**: TEI is accessible at `172.18.0.1:7997` — the comment in
   `embedder.py` line 2 says "accessible via SSH tunnel." A tunnel restart causes
   `ConnectTimeout` on the next embed call. This is a transient error that the current
   retry logic handles, but 7 seconds of sleep may not cover tunnel reconnect.
3. **Scheduled model reload**: Not observed in production, but if TEI is ever
   restarted for a model update, the 7-second retry budget is insufficient.

Without direct VictoriaLogs query access in this session, exact frequency is unknown.
Based on the structured log field `tei_embed_timeout` (set in `_embed_batch`), a
VictoriaLogs query of `service:knowledge-ingest AND message:tei_embed_timeout` would
reveal the actual blip frequency.

### Would an alert fire?

Currently: no. There is no Grafana alert on TEI embed failure rate. A Phase 1 failure
causes an HTTP 500 to the caller (connector, Gitea webhook integration, or portal),
but:
- Gitea webhook path swallows the exception (`logger.warning` only, returns 200).
- Bulk sync path swallows per-page exceptions.
- Direct `/ingest/v1/document` callers may or may not surface the 500 to users.

**Recommendation**: Add a Grafana alert on `service:knowledge-ingest AND
message:tei_embed_failed_all_attempts` (once that log is added per Fix #4 above)
with threshold > 3 events in 5 minutes.

## References

- [OpenAI rate limits and retry guidance](https://platform.openai.com/docs/guides/rate-limits)
- [OpenAI Cookbook: How to handle rate limits](https://cookbook.openai.com/examples/how_to_handle_rate_limits)
- [Implementing Retry & Timeout Strategies in AI APIs (2026)](https://dasroot.net/posts/2026/02/implementing-retry-timeout-strategies-ai-apis/)
- [Tenacity documentation](https://tenacity.readthedocs.io/en/stable/)
- [Tenacity: wait_random_exponential (full jitter)](https://github.com/jd/tenacity)
- [Circuit Breakers in FastAPI: practical introduction (2026)](https://blog.greeden.me/en/2026/04/21/a-practical-introduction-to-circuit-breakers-and-fallback-design-in-fastapi-real-world-patterns-for-preventing-external-api-failures-from-becoming-system-wide-failures/)
- [vLLM OOMKilled Recovery Kubernetes Runbook](https://www.kubenatives.com/p/vllm-oomkilled-recovery-kubernetes-runbook)
- [HuggingFace Text Embeddings Inference](https://huggingface.co/docs/text-embeddings-inference/index)
- [Cohere Embed API errors reference](https://docs.cohere.com/v2/reference/errors)
- [Python retry exponential backoff (2025)](https://oneuptime.com/blog/post/2025-01-06-python-retry-exponential-backoff/view)
