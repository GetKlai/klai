# Backend Pitfalls

> Python async services (FastAPI, httpx, asyncio) in klai-mono.

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [backend-async-sequential-loop](#backend-async-sequential-loop) | MED | Use `asyncio.gather`, not `await` in a for loop |
| [backend-async-no-per-call-timeout](#backend-async-no-per-call-timeout) | MED | Always set `timeout=` on external httpx calls |
| [backend-config-default-vs-env](#backend-config-default-vs-env) | LOW | Config defaults should not silently override env vars |
| [backend-prometheus-global-registry-tests](#backend-prometheus-global-registry-tests) | HIGH | Never use the global prometheus_client registry in tests |
| [backend-sendbeacon-no-auth-header](#backend-sendbeacon-no-auth-header) | HIGH | `navigator.sendBeacon` cannot send Authorization headers — design the endpoint as intentionally unauthenticated |

---

## backend-async-sequential-loop

**Severity:** MEDIUM

**Problem:** `await` calls inside a `for` loop execute sequentially. When fetching multiple external resources (URLs, API calls), total latency is the sum of all individual calls.

```python
# WRONG — sequential, latency = sum(all fetches)
for url in urls:
    result = await fetch(url)   # waits for each before starting the next
    results.append(result)
```

**Fix:** Use `asyncio.gather` to run all fetches in parallel:

```python
# CORRECT — parallel, latency = max(all fetches)
results = await asyncio.gather(*[fetch(url) for url in urls])
```

**Seen in:** `focus/research-api` web mode — 5 sequential docling URL fetches caused 25-50s latency. After parallelising: ~5-10s.

---

## backend-async-no-per-call-timeout

**Severity:** MEDIUM

**Problem:** `asyncio.gather` runs tasks in parallel but still waits for all of them to finish. If one external call is slow (e.g. 60s httpx timeout), the entire gather blocks until that task times out.

```python
# RISKY — one slow URL blocks the whole gather for up to 120s
results = await asyncio.gather(*[docling.convert_url(url) for url in urls])
```

**Fix:** Wrap each task with `asyncio.wait_for` to enforce a per-call deadline:

```python
_TIMEOUT = 15.0  # seconds per call

async def _fetch(url: str):
    try:
        return await asyncio.wait_for(docling.convert_url(url), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Timeout fetching: %s", url)
        return None

results = await asyncio.gather(*[_fetch(url) for url in urls])
```

The outer httpx/service timeout (e.g. 120s) is a safety net — the `wait_for` is the real deadline.

**Seen in:** `focus/research-api` web mode — added `_WEB_URL_TIMEOUT = 15.0` after parallelising.

---

## backend-config-default-vs-env

**Severity:** LOW

**Problem:** A wrong default in `pydantic-settings` `BaseSettings` is silently masked by an env var override in production. The bug only surfaces in fresh deployments that don't have the env var set.

```python
# WRONG default — masked in production by SEARXNG_URL=http://searxng:8080
searxng_url: str = "http://searxng:8888"
```

**Fix:** Always set the default to the real production value. Verify by checking the actual service port (`docker ps`) before writing the default.

**Seen in:** `focus/research-api` config — default port was 8888, actual container port 8080.

---

## backend-prometheus-global-registry-tests

**Severity:** HIGH

**Problem:** `prometheus_client` uses a global `REGISTRY` by default. If two tests register the same metric name, the second test raises `ValueError: Duplicated timeseries`. Tests also bleed state into each other — counters from test A affect assertions in test B.

```python
# WRONG — uses global registry, breaks on second test run
lcp = Histogram("webvitals_lcp_seconds", "LCP", ["page", "rating"],
    buckets=(...))
```

**Fix:** Use a dedicated `CollectorRegistry` per instantiation, wrapped in a dataclass. Patch the module-level instance in tests via `autouse` fixture.

```python
# vitals.py — dedicated registry per instance
from dataclasses import dataclass, field
from prometheus_client import CollectorRegistry, Histogram

@dataclass
class VitalsMetrics:
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    lcp: Histogram = field(init=False)

    def __post_init__(self) -> None:
        self.lcp = Histogram("webvitals_lcp_seconds", "LCP", ["page", "rating"],
            buckets=(...), registry=self.registry)

_metrics = VitalsMetrics()  # module-level singleton
```

```python
# test_vitals.py — fresh instance per test via autouse patch
import pytest
from unittest.mock import patch
from app.api import vitals as vitals_mod
from app.api.vitals import VitalsMetrics

@pytest.fixture(autouse=True)
def _fresh_metrics():
    fresh = VitalsMetrics()
    with patch.object(vitals_mod, "_metrics", fresh):
        yield
```

**Seen in:** `portal/backend/app/api/vitals.py` — SPEC-PERF-001 Web Vitals pipeline.

---

## backend-sendbeacon-no-auth-header

**Severity:** HIGH

**Trigger:** Building a browser → backend analytics or monitoring endpoint that requires authentication

`navigator.sendBeacon` is the correct API for sending telemetry on page unload (`visibilitychange → hidden`). It cannot set custom headers — including `Authorization`. Any backend endpoint that requires a Bearer token will reject every beacon request with 401.

**Why it happens:**
`sendBeacon` is a fire-and-forget, non-blocking API designed for the page-close scenario. It deliberately does not support `fetch`-style request options (no headers, no credentials override). The browser controls the request.

**Prevention:**
1. Design analytics/vitals ingest endpoints as intentionally unauthenticated. Accept this and document it explicitly in the router.
2. Add rate limiting at the reverse proxy (Caddy) as the primary protection against abuse:
   ```
   # Caddy — 60 req/min per IP for the vitals endpoint
   rate_limit /api/vitals 60r/m
   ```
3. Validate and clamp incoming values at the FastAPI layer (Pydantic `Field(ge=0, le=60000)`) to limit damage from bad data.
4. If you need authentication, use `fetch()` instead (with `keepalive: true` as a substitute for sendBeacon reliability) — but note that `keepalive` requests are dropped if the payload exceeds 64 KB.

**Correct pattern:**
```python
# vitals.py — intentionally unauthenticated; rate limiting is the only guard
@router.post("/api/vitals", status_code=204)
async def ingest_vitals(
    metrics: Annotated[list[VitalMetric], Body(max_length=10)],
) -> Response:
    """Receive a batch of Web Vitals metrics from the browser.
    Unauthenticated by design: navigator.sendBeacon cannot set headers.
    Rate limited at the Caddy layer.
    """
    ...
```

**Seen in:** `portal/backend/app/api/vitals.py` — SPEC-PERF-001 Web Vitals pipeline.

**See also:** MDN sendBeacon docs, `patterns/platform.md#platform-caddy-tenant-routing` for Caddy config

---
