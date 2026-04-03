---
paths:
  - "**/*.py"
  - "**/pyproject.toml"
---
# Backend Patterns

> Copy-paste solutions for Python/FastAPI services in klai-mono.

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use | Evidence |
|---|---|---|
| [backend-prometheus-fastapi-metrics](#backend-prometheus-fastapi-metrics) | Adding Prometheus metrics + `/metrics` endpoint to a FastAPI service | `curl /metrics` returns valid Prometheus text |
| [backend-two-phase-crawl-ai-fallback](#backend-two-phase-crawl-ai-fallback) | Crawling a page when content selectors are unknown and word count may be too low | Response `word_count >= _MIN_WORDS` for test URL |
| [backend-independent-session-fire-and-forget](#backend-independent-session-fire-and-forget) | Writing audit/analytics records that must survive caller exceptions | Audit row persists after endpoint returns 4xx/5xx |

---

## backend-prometheus-fastapi-metrics

**When to use:** Adding Prometheus histograms/counters to a FastAPI service and exposing a `/metrics` endpoint for Alloy/Prometheus to scrape

The standard `prometheus_client` global `REGISTRY` causes `ValueError: Duplicated timeseries` when tests run sequentially. The fix is a dataclass that creates a fresh `CollectorRegistry` per instantiation, with a module-level singleton for production and a per-test patch in tests.

```python
# vitals.py (or any metrics module)
from dataclasses import dataclass, field
from fastapi import APIRouter, Response
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

@dataclass
class ServiceMetrics:
    """Holds all Prometheus collectors. One instance = one registry."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)

    # Declare fields without init so __post_init__ creates them with the registry
    request_duration: Histogram = field(init=False)
    requests_total: Counter = field(init=False)

    def __post_init__(self) -> None:
        self.request_duration = Histogram(
            "service_request_duration_seconds",
            "Request duration in seconds",
            ["route", "status"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=self.registry,          # always pass registry explicitly
        )
        self.requests_total = Counter(
            "service_requests_total",
            "Total requests",
            registry=self.registry,
        )


# Module-level singleton — used by the router in production
_metrics = ServiceMetrics()

router = APIRouter()


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Prometheus scrape endpoint. Not behind auth — Alloy scrapes internally."""
    return Response(
        content=generate_latest(_metrics.registry),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
```

```python
# test_service.py — fresh metrics per test via autouse patch
import pytest
from unittest.mock import patch
from app.api import service as service_mod
from app.api.service import ServiceMetrics

@pytest.fixture(autouse=True)
def _fresh_metrics():
    fresh = ServiceMetrics()
    with patch.object(service_mod, "_metrics", fresh):
        yield
```

**Alloy scrape config** (add a block per service in `deploy/alloy/config.alloy`):
```
prometheus.scrape "portal_api" {
  targets = [{ __address__ = "portal-api:8010" }]
  forward_to = [prometheus.remote_write.victoriametrics.receiver]
  scrape_interval = "15s"
  metrics_path = "/metrics"
}
```

**Key rules:**
- Always pass `registry=self.registry` to every collector — never use the implicit global registry
- The `/metrics` endpoint does not need authentication when scraping is internal (Alloy → service on the same Docker network)
- ms-to-seconds conversion: browser APIs (LCP, FCP, INP, TTFB) report milliseconds; Prometheus convention is seconds. Divide by 1000 before `observe()`.
- CLS is unitless — store as-is, no conversion needed

**Rule:** One `CollectorRegistry` per `ServiceMetrics` instance. Never register metrics on the default global registry in a service with tests.

**See also:** `pitfalls/backend.md#backend-prometheus-global-registry-tests`

---

## backend-two-phase-crawl-ai-fallback

**When to use:** Crawling a page when you don't know the content selector upfront and a full-page crawl may return too few words (navigation-heavy or JS-heavy sites)

Run a first crawl with the full pipeline (no selector). If `word_count` is below a threshold, extract a DOM summary and ask an LLM to identify the content selector. Re-crawl with the detected selector and return the best result. Keep the entire flow synchronous inside the endpoint — no background tasks — so the response always reflects the best result achieved.

```python
_MIN_WORDS = 100  # below this, try AI selector detection

async def crawl_preview(body: CrawlPreviewRequest) -> CrawlPreviewResponse:
    # Phase 1 — full pipeline, no selector
    result = await _run_crawl(url=body.url, selector=body.selector or None)

    if result.word_count >= _MIN_WORDS or body.selector:
        return result  # good enough, or caller already provided a selector

    # Phase 2 — AI fallback: extract DOM summary, ask LLM for selector
    dom_summary = await _extract_dom_summary(body.url)
    detected = await _ask_llm_for_selector(dom_summary)  # returns None on failure

    if detected:
        result2 = await _run_crawl(url=body.url, selector=detected)
        if result2.word_count >= _MIN_WORDS:
            await domain_selectors.store(org_id, domain, detected)  # persist for future
            return result2

    return result  # return phase-1 result if AI fallback didn't improve things
```

**DOM summary for LLM input** — rank elements by word count, take the top N:
```python
async def _extract_dom_summary(url: str) -> str:
    """Return a ranked list of DOM elements with their word counts for LLM analysis."""
    # Use headless browser to get rendered DOM
    # Score each element: word_count * depth_penalty
    # Return top 25 as a compact text block: "tag.class#id: N words"
```

**LLM prompt shape** (send to `klai-fast` — short structured output):
```
Given these DOM elements ranked by word count:
{dom_summary}

Return the single CSS selector most likely to contain the main article content.
Reply with ONLY the selector string, nothing else.
```

**Key rules:**
- Use `klai-fast` for selector detection — it's a short structured output task, not user-facing synthesis.
- Only persist the AI-detected selector if the re-crawl actually meets the word threshold — don't store selectors that didn't help.
- The threshold for storing is the same as the threshold for triggering the fallback (`_MIN_WORDS`).
- Keeping this synchronous (not a background task) means callers always get the best available result in one request.

**Rule:** Two-phase crawl: full pipeline first, AI selector detection only when word count is too low. Store the selector only on confirmed success.

**See also:** `pitfalls/backend.md#backend-crawl4ai-class-substring-selectors`

---

## backend-independent-session-fire-and-forget

**When to use:** Writing audit logs, analytics events, or other observational records that must persist even when the request endpoint raises an exception

The request-scoped database session rolls back on any unhandled exception (including `HTTPException`). SAVEPOINTs (`begin_nested()`) are also discarded on outer rollback. Use an independent `AsyncSessionLocal()` that opens and commits its own transaction.

```python
import json
import structlog
from sqlalchemy import text
from app.database import AsyncSessionLocal

logger = structlog.get_logger()

async def log_event(
    action: str,
    user_id: str,
    details: dict | None = None,
    org_id: str | None = None,
) -> None:
    """Fire-and-forget audit write. Independent session survives caller rollback."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO portal_audit_log (action, user_id, org_id, details)
                    VALUES (:action, :user_id, :org_id, CAST(:details AS jsonb))
                """),
                {
                    "action": action,
                    "user_id": user_id,
                    "org_id": org_id,
                    "details": json.dumps(details) if details else None,
                },
            )
            await session.commit()
    except Exception:
        logger.warning("audit_log_failed", action=action, exc_info=True)
```

**Key rules:**
- Use `text()` raw SQL if the table has RLS — SQLAlchemy ORM adds implicit `RETURNING` which triggers SELECT policies (see `pitfalls/backend.md#backend-sqlalchemy-returning-rls`)
- Use `CAST(:param AS jsonb)` instead of `::jsonb` — the `::` syntax conflicts with SQLAlchemy's `:param` binding
- Never let audit failures crash the business endpoint — always wrap in try/except
- The independent session is short-lived: open, insert, commit, close. No long-running connections.

**Rule:** Observational writes (audit, analytics) that must survive caller exceptions need their own session and transaction, decoupled from the request lifecycle.

**See also:** `pitfalls/backend.md#backend-request-session-rollback-loses-writes`, `pitfalls/backend.md#backend-sqlalchemy-returning-rls`

---

## See Also

- [patterns/devops.md](devops.md) - Deployments, Docker
- [patterns/platform.md](platform.md) - LiteLLM, Grafana, VictoriaMetrics
- [pitfalls/backend.md](../pitfalls/backend.md) - Python async and FastAPI pitfalls
