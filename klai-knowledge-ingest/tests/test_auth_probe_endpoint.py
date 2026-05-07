"""Tests for ``POST /ingest/v1/crawl/auth-probe`` (REQ-2 endpoint).

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-2 / REQ-6.

The endpoint mirrors ``/ingest/v1/crawl/preview`` but classifies the result
into one of five outcome labels:

- ``auth_ok``
- ``auth_failed_no_cookies``
- ``auth_failed_still_walled``
- ``auth_failed_credentials_invalid``
- ``auth_failed_unreachable``

Identity assertion uses ``assert_caller_identity_tenant_only`` per
bug-pattern memory (no end-user on this internal route).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from knowledge_ingest.crawl4ai_client import CrawlResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_crawl_result(
    *,
    url: str = "https://example.com/page",
    fit_markdown: str = "",
    raw_markdown: str = "",
    html: str = "",
    word_count: int = 0,
    success: bool = True,
    metadata: dict | None = None,
    response_headers: dict[str, str] | None = None,
) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=fit_markdown,
        raw_markdown=raw_markdown,
        html=html,
        word_count=word_count,
        success=success,
        metadata=metadata or {},
        response_headers=response_headers or {},
    )


# ---------------------------------------------------------------------------
# Outcome labels
# ---------------------------------------------------------------------------


def test_auth_probe_returns_auth_ok_for_healthy_public_page(client: TestClient) -> None:
    healthy = _make_crawl_result(
        fit_markdown="A long article body. " * 60,
        raw_markdown="A long article body. " * 60,
        word_count=600,
        metadata={"status_code": 200},
        response_headers={"content-type": "text/html"},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=healthy),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["classification"] == "auth_ok"
    assert body["match_reasons"] == []
    assert body["word_count"] == 600


def test_auth_probe_returns_auth_failed_no_cookies_when_walled_and_no_cookies(
    client: TestClient,
) -> None:
    walled = _make_crawl_result(
        fit_markdown="Article teaser. " * 5 + "Log in to read this article",
        word_count=80,
        metadata={"status_code": 200},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=walled),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "auth_failed_no_cookies"
    assert "end_of_body_login_marker" in body["match_reasons"]


def test_auth_probe_returns_auth_failed_still_walled_when_walled_with_cookies(
    client: TestClient,
) -> None:
    walled = _make_crawl_result(
        fit_markdown="Inloggen om verder te lezen",
        word_count=10,
        metadata={"status_code": 200},
        response_headers={"set-cookie": "session=abc; Path=/"},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=walled),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": [{"name": "session", "value": "expired"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "auth_failed_still_walled"
    assert "end_of_body_login_marker" in body["match_reasons"]
    assert "session_cookie_minimal_body" in body["match_reasons"]


def test_auth_probe_returns_auth_failed_credentials_invalid_on_401(
    client: TestClient,
) -> None:
    bad = _make_crawl_result(
        fit_markdown="",
        word_count=0,
        success=False,
        metadata={"status_code": 401},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=bad),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": [{"name": "session", "value": "wrong"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "auth_failed_credentials_invalid"
    assert "http_unauthenticated" in body["match_reasons"]


def test_auth_probe_returns_auth_failed_unreachable_on_connection_error(
    client: TestClient,
) -> None:
    """Empty/zero-word result with no status — unreachable."""
    unreachable = _make_crawl_result(
        fit_markdown="",
        word_count=0,
        success=False,
        metadata={},  # no status_code
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=unreachable),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "auth_failed_unreachable"


# ---------------------------------------------------------------------------
# Auth guard side effect
# ---------------------------------------------------------------------------


def test_auth_probe_returns_auth_guard_when_auth_ok_with_cookies(
    client: TestClient,
) -> None:
    healthy_with_cookies = _make_crawl_result(
        fit_markdown="A long article body. " * 60,
        raw_markdown="A long article body. " * 60,
        word_count=600,
        metadata={"status_code": 200},
    )
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=healthy_with_cookies),
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_dom_summary",
            new=AsyncMock(return_value=None),  # AI detection skipped
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": [{"name": "sess", "value": "valid"}],
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["classification"] == "auth_ok"
    assert body["auth_guard"] is not None
    assert body["auth_guard"]["canary_url"] == "https://example.com/page"
    assert body["auth_guard"]["canary_fingerprint"] is not None


def test_auth_probe_does_not_return_auth_guard_when_no_cookies(
    client: TestClient,
) -> None:
    """Without cookies there is nothing to canary against — auth_guard MUST be None."""
    healthy = _make_crawl_result(
        fit_markdown="A long article body. " * 60,
        raw_markdown="A long article body. " * 60,
        word_count=600,
        metadata={"status_code": 200},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=healthy),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page"},
        )
    body = resp.json()
    assert body["classification"] == "auth_ok"
    assert body["auth_guard"] is None


# ---------------------------------------------------------------------------
# Security & validation
# ---------------------------------------------------------------------------


def test_auth_probe_rejects_internal_secret_missing(client: TestClient) -> None:
    """SPEC-SEC-011 — missing ``X-Internal-Secret`` is rejected.

    ``client`` fixture injects the header by default; we drop it for this
    one assertion to ensure the middleware still guards this new endpoint.
    """
    resp = client.post(
        "/ingest/v1/crawl/auth-probe",
        json={"url": "https://example.com/page"},
        headers={"X-Internal-Secret": "wrong-secret"},
    )
    assert resp.status_code in (401, 403)


def test_auth_probe_rejects_ssrf_url(client: TestClient) -> None:
    """SPEC-SEC-SSRF-001 parity — auth-probe MUST reject internal-host URLs
    before any crawl call (the same Cornelis AC-6 guard that protects
    /ingest/v1/crawl and /ingest/v1/crawl/preview)."""
    spy = AsyncMock(side_effect=AssertionError("SSRF guard should short-circuit"))
    with patch("knowledge_ingest.routes.crawl.crawl_page", new=spy):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "http://docker-socket-proxy:2375/v1.42/info"},
        )
    assert resp.status_code == 400
    assert spy.await_count == 0


def test_auth_probe_returns_match_reasons_list(client: TestClient) -> None:
    """The response shape always exposes ``match_reasons`` as a list (possibly empty)."""
    healthy = _make_crawl_result(
        fit_markdown="Real article. " * 60,
        word_count=600,
        metadata={"status_code": 200},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=healthy),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page"},
        )
    body = resp.json()
    assert isinstance(body["match_reasons"], list)


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provided_org_id", ["org-test-123", "org-test-456"])
def test_auth_probe_passes_org_id_to_identity_assert(
    client: TestClient, provided_org_id: str
) -> None:
    """When org_id is supplied in the body, identity assert is called with
    tenant-only flavor (no claimed_user_id)."""
    healthy = _make_crawl_result(
        fit_markdown="Real article. " * 60,
        word_count=600,
        metadata={"status_code": 200},
    )
    spy = AsyncMock(return_value=None)
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=healthy),
        ),
        patch(
            "knowledge_ingest.routes.crawl.assert_caller_identity_tenant_only",
            new=spy,
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page", "org_id": provided_org_id},
        )
    assert resp.status_code == 200, resp.text
    assert spy.await_count == 1
    call_kwargs = spy.await_args.kwargs
    assert call_kwargs.get("claimed_org_id") == provided_org_id
