"""Tests for ``POST /ingest/v1/crawl/auth-probe`` (REQ-2 endpoint).

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-2 / REQ-6.

The endpoint validates that operator-supplied cookies actually authenticate
against the seed URL, by fetching the URL twice via plain httpx — once
WITH cookies, once WITHOUT — and comparing the responses. Significant
divergence (word count, byte size, or status-code split) means cookies
have an effect; identical responses mean they do not.

Why httpx and not crawl4ai/Playwright: the validation only needs HTTP-
level cookie behaviour; the underlying authenticated crawl runs through
crawl4ai with native ``BrowserConfig.cookies`` separately.

Outcome labels (consumed by the wizard frontend):

- ``auth_ok``                    — cookies measurably change the response
- ``auth_failed_no_cookies``     — no cookies provided
- ``auth_failed_still_walled``   — cookies provided but have no effect
- ``auth_failed_unreachable``    — fetch raised before classification

Identity assertion uses ``assert_caller_identity_tenant_only`` per
bug-pattern memory (no end-user on this internal-secret route).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from knowledge_ingest.routes.crawl import _ProbeResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _probe(
    *,
    status_code: int = 200,
    word_count: int = 100,
    byte_size: int = 5000,
    text: str = "Sample response body",
) -> _ProbeResponse:
    return _ProbeResponse(
        status_code=status_code,
        word_count=word_count,
        byte_size=byte_size,
        text=text,
    )


def _patch_probe_fetch(with_cookies: _ProbeResponse, without_cookies: _ProbeResponse):
    """Patch ``_probe_fetch`` to return ``with_cookies`` when cookies are
    passed and ``without_cookies`` when they are not.
    """

    async def _side_effect(
        url: str, pin_map: dict[str, str], cookies: dict[str, str] | None = None
    ) -> _ProbeResponse:
        return with_cookies if cookies else without_cookies

    return patch(
        "knowledge_ingest.routes.crawl._probe_fetch",
        new=AsyncMock(side_effect=_side_effect),
    )


_REDCACTUS_COOKIES = [
    {
        "name": "prod-knowledgebase-session",
        "value": "eyJ.fake.value",
        "domain": "wiki.redcactus.cloud",
        "path": "/",
    },
    {
        "name": "XSRF-TOKEN",
        "value": "eyJ.fake.value",
        "domain": "wiki.redcactus.cloud",
        "path": "/",
    },
]


# ---------------------------------------------------------------------------
# Outcome labels
# ---------------------------------------------------------------------------


def test_auth_probe_no_cookies_short_circuits_without_fetching(
    client: TestClient,
) -> None:
    """When no cookies are supplied, classification is decided without any
    HTTP fetch — no point comparing anonymous to anonymous."""
    spy = AsyncMock(side_effect=AssertionError("should not fetch when no cookies"))
    with patch("knowledge_ingest.routes.crawl._probe_fetch", new=spy):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": "https://example.com/page"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["classification"] == "auth_failed_no_cookies"
    assert spy.await_count == 0
    assert body["auth_guard"] is None


def test_auth_probe_significant_word_diff_returns_auth_ok(client: TestClient) -> None:
    """Cookies that produce a measurably larger response authenticate."""
    with _patch_probe_fetch(
        with_cookies=_probe(
            word_count=7299, byte_size=181000, text="Logged in body. " * 500
        ),
        without_cookies=_probe(
            word_count=5572, byte_size=140000, text="Anonymous body. " * 300
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://wiki.redcactus.cloud/nl/article",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert body["classification"] == "auth_ok"
    assert body["word_count"] == 7299
    assert body["auth_guard"] is not None
    assert body["auth_guard"]["canary_url"] == "https://wiki.redcactus.cloud/nl/article"


def test_auth_probe_status_code_split_returns_auth_ok(client: TestClient) -> None:
    """A status-code split (200 with cookies, 302 without) is itself a strong
    auth signal even if the response bodies happen to be similar size."""
    with _patch_probe_fetch(
        with_cookies=_probe(status_code=200, word_count=300, byte_size=10000),
        without_cookies=_probe(status_code=302, word_count=290, byte_size=9800),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/dashboard",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert body["classification"] == "auth_ok"


def test_auth_probe_no_diff_returns_auth_failed_still_walled(
    client: TestClient,
) -> None:
    """Cookies that produce an identical (or near-identical) response to the
    anonymous baseline have no authenticating effect — the wizard MUST NOT
    green-light this case."""
    identical = _probe(
        word_count=250, byte_size=10000, text="Same body for both fetches."
    )
    with _patch_probe_fetch(with_cookies=identical, without_cookies=identical):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://wiki.redcactus.cloud/nl/",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert body["classification"] == "auth_failed_still_walled"
    assert body["auth_guard"] is None


def test_auth_probe_tiny_diff_under_threshold_is_still_walled(
    client: TestClient,
) -> None:
    """5%-of-baseline floor: a 10-word diff on a 5000-word page is noise,
    not a signal. The bare minimum is max(20 words, 5% of baseline)."""
    with _patch_probe_fetch(
        with_cookies=_probe(word_count=5010, byte_size=140100),
        without_cookies=_probe(word_count=5000, byte_size=140000),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://wiki.redcactus.cloud/nl/",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert body["classification"] == "auth_failed_still_walled"


def test_auth_probe_fetch_failure_returns_auth_failed_unreachable(
    client: TestClient,
) -> None:
    """If both fetches raise (DNS failure, TLS error, timeout), the classifier
    reports unreachable instead of guessing."""
    failing = AsyncMock(side_effect=ConnectionError("connection refused"))
    with patch("knowledge_ingest.routes.crawl._probe_fetch", new=failing):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert body["classification"] == "auth_failed_unreachable"
    assert body["auth_guard"] is None


# ---------------------------------------------------------------------------
# auth_guard
# ---------------------------------------------------------------------------


def test_auth_probe_auth_ok_emits_canary_fingerprint(client: TestClient) -> None:
    """auth_ok with cookies must produce a canary fingerprint for downstream
    cron-sync to detect cookie expiration without re-running the diff."""
    with _patch_probe_fetch(
        with_cookies=_probe(
            word_count=1000,
            byte_size=30000,
            text="Long unique logged-in content. " * 200,
        ),
        without_cookies=_probe(
            word_count=300, byte_size=8000, text="Short anonymous body. " * 50
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert body["classification"] == "auth_ok"
    assert body["auth_guard"] is not None
    assert body["auth_guard"]["canary_url"] == "https://example.com/page"
    assert body["auth_guard"]["canary_fingerprint"]


def test_auth_probe_no_cookies_does_not_emit_canary(client: TestClient) -> None:
    """Without cookies the auth_guard is None — fingerprinting an anonymous
    response would be useless for the cron-sync."""
    resp = client.post(
        "/ingest/v1/crawl/auth-probe",
        json={"url": "https://example.com/page"},
    )
    body = resp.json()
    assert body["auth_guard"] is None


# ---------------------------------------------------------------------------
# Security guards
# ---------------------------------------------------------------------------


def test_auth_probe_rejects_internal_secret_missing(client: TestClient) -> None:
    """SPEC-SEC-011 — missing/wrong ``X-Internal-Secret`` is rejected by
    middleware before the route runs."""
    resp = client.post(
        "/ingest/v1/crawl/auth-probe",
        json={"url": "https://example.com/page"},
        headers={"X-Internal-Secret": "wrong-secret"},
    )
    assert resp.status_code in (401, 403)


def test_auth_probe_rejects_ssrf_url(client: TestClient) -> None:
    """SPEC-SEC-SSRF-001 parity — auth-probe MUST reject internal-host URLs
    before any HTTP fetch, mirroring the Cornelis AC-6 guard on /preview."""
    spy = AsyncMock(side_effect=AssertionError("SSRF guard should short-circuit"))
    with patch("knowledge_ingest.routes.crawl._probe_fetch", new=spy):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "http://docker-socket-proxy:2375/v1.42/info",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    assert resp.status_code == 400
    assert spy.await_count == 0


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_auth_probe_match_reasons_is_list(client: TestClient) -> None:
    """``match_reasons`` is always a list, populated with diagnostic strings."""
    with _patch_probe_fetch(
        with_cookies=_probe(word_count=1000, byte_size=20000),
        without_cookies=_probe(word_count=300, byte_size=8000),
    ):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": _REDCACTUS_COOKIES,
            },
        )
    body = resp.json()
    assert isinstance(body["match_reasons"], list)
    assert len(body["match_reasons"]) >= 2  # diagnostic detail attached


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provided_org_id", ["org-test-123", "org-test-456"])
def test_auth_probe_passes_org_id_to_identity_assert(
    client: TestClient, provided_org_id: str
) -> None:
    """When ``org_id`` is supplied, ``assert_caller_identity_tenant_only``
    is called with that value (no end-user / claimed_user_id)."""
    spy = AsyncMock(return_value=None)
    with (
        patch(
            "knowledge_ingest.routes.crawl.assert_caller_identity_tenant_only",
            new=spy,
        ),
        _patch_probe_fetch(
            with_cookies=_probe(word_count=1000, byte_size=20000),
            without_cookies=_probe(word_count=300, byte_size=8000),
        ),
    ):
        client.post(
            "/ingest/v1/crawl/auth-probe",
            json={
                "url": "https://example.com/page",
                "cookies": _REDCACTUS_COOKIES,
                "org_id": provided_org_id,
            },
        )
    assert spy.await_count == 1
    _, kwargs = spy.await_args
    assert kwargs.get("claimed_org_id") == provided_org_id
