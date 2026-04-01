"""Web Vitals ingestion and Prometheus metrics exposition.

POST /api/perf   — receives web-vitals reports from the browser.
GET /metrics     — exposes Prometheus-format metrics for scraping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Response
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Metrics that arrive in milliseconds and must be converted to seconds.
# CLS is a unitless score and is stored as-is.
# ---------------------------------------------------------------------------

_MS_METRICS: frozenset[str] = frozenset({"LCP", "FCP", "INP", "TTFB"})

# ---------------------------------------------------------------------------
# Pydantic model
# ---------------------------------------------------------------------------


class VitalMetric(BaseModel):
    name: Literal["LCP", "FCP", "INP", "CLS", "TTFB"]
    value: float = Field(ge=0, le=60000)
    rating: Literal["good", "needs-improvement", "poor"]
    page: str = Field(max_length=256)


# ---------------------------------------------------------------------------
# Metrics container — creates fresh collectors on a dedicated registry
# ---------------------------------------------------------------------------


@dataclass
class VitalsMetrics:
    """Holds all Prometheus collectors for Web Vitals."""

    registry: CollectorRegistry = field(default_factory=CollectorRegistry)

    lcp: Histogram = field(init=False)
    fcp: Histogram = field(init=False)
    inp: Histogram = field(init=False)
    cls: Histogram = field(init=False)
    ttfb: Histogram = field(init=False)
    reports: Counter = field(init=False)
    histograms: dict[str, Histogram] = field(init=False)

    def __post_init__(self) -> None:
        self.lcp = Histogram(
            "webvitals_lcp_seconds",
            "Largest Contentful Paint in seconds",
            ["page", "rating"],
            buckets=(0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 8.0, 10.0),
            registry=self.registry,
        )
        self.fcp = Histogram(
            "webvitals_fcp_seconds",
            "First Contentful Paint in seconds",
            ["page", "rating"],
            buckets=(0.25, 0.5, 0.75, 1.0, 1.5, 1.8, 2.5, 3.0, 5.0),
            registry=self.registry,
        )
        self.inp = Histogram(
            "webvitals_inp_seconds",
            "Interaction to Next Paint in seconds",
            ["page", "rating"],
            buckets=(0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.75, 1.0),
            registry=self.registry,
        )
        self.cls = Histogram(
            "webvitals_cls_score",
            "Cumulative Layout Shift score",
            ["page", "rating"],
            buckets=(0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 1.0),
            registry=self.registry,
        )
        self.ttfb = Histogram(
            "webvitals_ttfb_seconds",
            "Time to First Byte in seconds",
            ["page", "rating"],
            buckets=(0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 1.8, 2.5),
            registry=self.registry,
        )
        self.reports = Counter(
            "webvitals_reports_total",
            "Total number of Web Vitals report batches received",
            registry=self.registry,
        )
        self.histograms = {
            "LCP": self.lcp,
            "FCP": self.fcp,
            "INP": self.inp,
            "CLS": self.cls,
            "TTFB": self.ttfb,
        }


# Module-level singleton used by the router at runtime.
_metrics = VitalsMetrics()

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.post("/api/perf", status_code=204)
async def ingest_vitals(
    metrics: Annotated[list[VitalMetric], Body(max_length=10)],
) -> Response:
    """Receive a batch of Web Vitals metrics from the browser."""
    for m in metrics:
        histogram = _metrics.histograms[m.name]
        value = m.value / 1000.0 if m.name in _MS_METRICS else m.value
        histogram.labels(page=m.page, rating=m.rating).observe(value)

    _metrics.reports.inc()
    return Response(status_code=204)


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    """Expose Prometheus-format metrics for scraping."""
    return Response(
        content=generate_latest(_metrics.registry),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
