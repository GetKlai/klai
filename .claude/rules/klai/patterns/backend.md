---
paths: "**/*.py,**/pyproject.toml"
---
# Backend Patterns

> Copy-paste solutions for Python/FastAPI services in klai-mono.

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use |
|---|---|
| [backend-prometheus-fastapi-metrics](#backend-prometheus-fastapi-metrics) | Adding Prometheus metrics + `/metrics` endpoint to a FastAPI service |

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

## See Also

- [patterns/devops.md](devops.md) - Deployments, Docker
- [patterns/platform.md](platform.md) - LiteLLM, Grafana, VictoriaMetrics
- [pitfalls/backend.md](../pitfalls/backend.md) - Python async and FastAPI pitfalls
