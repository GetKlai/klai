"""SSRF regression tests for ``/ingest/v1/crawl/preview`` (SPEC-SEC-SSRF-001).

Covers:

- AC-1 — preview_crawl rejects unvalidated URLs before any downstream
  DNS lookup (400, not the historical 200-with-empty-body).
- AC-6 — the exact Cornelis exploit body is rejected, and the same
  body on the already-guarded ``/ingest/v1/crawl`` endpoint stays
  rejected (parity guard).

The test client is defined in ``conftest.py``. We patch ``_run_crawl``
and ``crawl_dom_summary`` so any call to them is an assertion failure
— the SSRF guard must short-circuit before either runs.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def no_crawl() -> AsyncMock:
    """Replace ``_run_crawl`` + ``crawl_dom_summary`` with assertion spies."""

    with (
        patch(
            "knowledge_ingest.routes.crawl._run_crawl",
            new=AsyncMock(side_effect=AssertionError("SSRF guard should have short-circuited")),
        ) as run_spy,
        patch(
            "knowledge_ingest.routes.crawl.crawl_dom_summary",
            new=AsyncMock(side_effect=AssertionError("SSRF guard should have short-circuited")),
        ),
    ):
        yield run_spy


@pytest.mark.parametrize(
    "url",
    [
        # AC-6: the exact Cornelis exploit.
        "http://docker-socket-proxy:2375/v1.42/info",
        # AC-6 scheme variant.
        "https://docker-socket-proxy:2375/v1.42/info",
        # AC-4 siblings (docker-internal hosts).
        "https://portal-api:8010/internal/v1/orgs",
        "https://redis:6379/info",
        "https://crawl4ai:11235/health",
        # AC-2 class reject (RFC1918 literal).
        "https://10.0.0.5/",
    ],
)
def test_preview_rejects_ssrf_urls(
    client: TestClient, no_crawl: AsyncMock, url: str
) -> None:
    """AC-1 + AC-6: preview returns 400 and never calls the crawl engine."""

    resp = client.post("/ingest/v1/crawl/preview", json={"url": url})
    assert resp.status_code == 400, (
        f"expected 400 SSRF rejection, got {resp.status_code}: {resp.text}"
    )
    assert no_crawl.await_count == 0


def test_crawl_url_ssrf_parity(client: TestClient, no_crawl: AsyncMock) -> None:
    """AC-6 last bullet: the guarded ``/ingest/v1/crawl`` stays guarded."""

    resp = client.post(
        "/ingest/v1/crawl",
        json={
            "org_id": "org-test",
            "kb_slug": "kb-test",
            "url": "http://docker-socket-proxy:2375/v1.42/info",
        },
    )
    assert resp.status_code == 400
    assert no_crawl.await_count == 0


def test_preview_http_scheme_rejected(
    client: TestClient, no_crawl: AsyncMock
) -> None:
    """Non-HTTPS URLs are rejected by the first guard check."""

    resp = client.post(
        "/ingest/v1/crawl/preview", json={"url": "http://example.com/path"}
    )
    assert resp.status_code == 400
    assert no_crawl.await_count == 0


def test_preview_no_hostname_rejected(
    client: TestClient, no_crawl: AsyncMock
) -> None:
    resp = client.post(
        "/ingest/v1/crawl/preview", json={"url": "https:///nohost"}
    )
    assert resp.status_code == 400
    assert no_crawl.await_count == 0
