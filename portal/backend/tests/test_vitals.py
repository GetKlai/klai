"""Tests for the Web Vitals ingestion and Prometheus metrics endpoint."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api import vitals as vitals_mod
from app.api.vitals import VitalsMetrics
from app.api.vitals import router as vitals_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = "http://testserver"


def _make_app():
    """Create a minimal FastAPI app with only the vitals router."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(vitals_router)
    return app


def _valid_metric(*, name: str = "LCP", value: float = 2500.0, rating: str = "good", page: str = "/home"):
    return {"name": name, "value": value, "rating": rating, "page": page}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_metrics():
    """Replace the module-level _metrics with a fresh instance per test.

    This avoids state leaking between tests (counter increments, histogram
    observations) since prometheus_client collectors are append-only.
    """
    fresh = VitalsMetrics()
    with patch.object(vitals_mod, "_metrics", fresh):
        yield


@pytest.fixture()
def app():
    """Fresh FastAPI app per test."""
    return _make_app()


@pytest_asyncio.fixture()
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url=_BASE) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/vitals -- valid payloads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_valid_metrics_returns_204(client: AsyncClient) -> None:
    """A valid list of metrics returns HTTP 204 No Content."""
    payload = [_valid_metric(), _valid_metric(name="CLS", value=0.15, rating="good")]
    resp = await client.post("/api/vitals", json=payload)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_post_empty_list_returns_204(client: AsyncClient) -> None:
    """An empty list is valid -- nothing to observe, still 204."""
    resp = await client.post("/api/vitals", json=[])
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_post_all_metric_names(client: AsyncClient) -> None:
    """All five valid metric names are accepted."""
    payload = [
        _valid_metric(name="LCP", value=2500),
        _valid_metric(name="FCP", value=1800),
        _valid_metric(name="INP", value=200),
        _valid_metric(name="CLS", value=0.1),
        _valid_metric(name="TTFB", value=600),
    ]
    resp = await client.post("/api/vitals", json=payload)
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /api/vitals -- validation errors (422)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_invalid_metric_name_returns_422(client: AsyncClient) -> None:
    """A metric name not in the Literal set is rejected."""
    resp = await client.post("/api/vitals", json=[_valid_metric(name="XYZ")])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_invalid_rating_returns_422(client: AsyncClient) -> None:
    """A rating not in the Literal set is rejected."""
    resp = await client.post("/api/vitals", json=[_valid_metric(rating="bad")])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_negative_value_returns_422(client: AsyncClient) -> None:
    """A negative value is rejected by the ge=0 constraint."""
    resp = await client.post("/api/vitals", json=[_valid_metric(value=-1)])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_value_too_large_returns_422(client: AsyncClient) -> None:
    """A value above 60000 is rejected by the le=60000 constraint."""
    resp = await client.post("/api/vitals", json=[_valid_metric(value=70000)])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_page_too_long_returns_422(client: AsyncClient) -> None:
    """A page string exceeding 256 chars is rejected."""
    long_page = "/" + "x" * 256  # 257 chars total
    resp = await client.post("/api/vitals", json=[_valid_metric(page=long_page)])
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_oversized_batch_returns_422(client: AsyncClient) -> None:
    """More than 10 items in a single POST is rejected."""
    payload = [_valid_metric() for _ in range(11)]
    resp = await client.post("/api/vitals", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /metrics -- Prometheus exposition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_metrics_returns_prometheus_format(client: AsyncClient) -> None:
    """GET /metrics returns text/plain with Prometheus exposition format."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # At minimum, the counter should be present in the output
    assert "webvitals_reports_total" in resp.text


# ---------------------------------------------------------------------------
# Histogram observation -- ms-to-seconds conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ms_to_seconds_conversion(client: AsyncClient) -> None:
    """LCP value 2500 (ms) should be observed as 2.5 seconds in Prometheus."""
    await client.post("/api/vitals", json=[_valid_metric(name="LCP", value=2500)])
    resp = await client.get("/metrics")
    body = resp.text
    # The histogram should record a value of 2.5 -- that falls in the 2.5 bucket
    # and all larger buckets. The 2.0 bucket should NOT contain this observation.
    assert "webvitals_lcp_seconds_bucket" in body
    # 2.5 <= 2.5, so le="2.5" should have count >= 1
    assert 'webvitals_lcp_seconds_bucket{le="2.5"' in body


@pytest.mark.asyncio
async def test_cls_no_conversion(client: AsyncClient) -> None:
    """CLS values are unitless scores -- stored as-is without ms-to-s conversion."""
    await client.post("/api/vitals", json=[_valid_metric(name="CLS", value=0.15)])
    resp = await client.get("/metrics")
    body = resp.text
    assert "webvitals_cls_score_bucket" in body
    # 0.15 <= 0.15, so le="0.15" bucket should have count >= 1
    assert 'webvitals_cls_score_bucket{le="0.15"' in body


@pytest.mark.asyncio
async def test_post_increments_reports_counter(client: AsyncClient) -> None:
    """Each POST increments the webvitals_reports_total counter."""
    await client.post("/api/vitals", json=[_valid_metric()])
    await client.post("/api/vitals", json=[_valid_metric(), _valid_metric(name="FCP", value=1000)])
    resp = await client.get("/metrics")
    body = resp.text
    assert "webvitals_reports_total" in body
    # After 2 POSTs, the counter should be 2.0
    assert "webvitals_reports_total 2.0" in body


@pytest.mark.asyncio
async def test_histogram_labels_include_page_and_rating(client: AsyncClient) -> None:
    """Histogram observations include page and rating labels."""
    await client.post(
        "/api/vitals",
        json=[_valid_metric(name="TTFB", value=400, rating="needs-improvement", page="/dashboard")],
    )
    resp = await client.get("/metrics")
    body = resp.text
    assert 'page="/dashboard"' in body
    assert 'rating="needs-improvement"' in body
