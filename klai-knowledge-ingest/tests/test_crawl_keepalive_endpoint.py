"""Tests for POST /ingest/v1/crawl/keep-alive.

Background: a connector's stored session (browser cookies pasted once into
the wizard) goes idle-timeout between the once-daily scheduled crawl and its
previous use — proved in production on the Voys/support RedCactus connector,
where a manual re-sync 45 minutes after a fresh cookie paste succeeded 100%
but every unattended daily run since failed with auth_wall_detected on ~43%
of pages. This endpoint is klai-connector's periodic "touch": one cheap
authenticated GET so the session never goes idle. It is best-effort — it
must never surface a 500 to the caller.

2026-09-25: the probe used to treat any bare 3xx as "not ok" without ever
following it. Production data (connector b369796b) showed the probed URL is
usually the site root, and many CMSes 302 that to a language path (``/en``)
even for an authenticated session — every tick reported ok=false and the
keep-alive never actually kept anything alive. These tests pin the fix: the
probe now follows same-site redirects and classifies the final page with the
shared ``classify_auth_wall`` heuristic instead of trusting a bare status
code.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from klai_image_storage.url_guard import ValidatedURL

from knowledge_ingest.connector_cookies import ConnectorNotFoundError
from knowledge_ingest.routes.crawl import _ProbeResponse
from tests.test_crawl_sync_endpoint import _client_with_patches, _make_pool

_VALIDATED = ValidatedURL(
    url="https://wiki.example.com/",
    hostname="wiki.example.com",
    pinned_ips=frozenset({"203.0.113.1"}),
    preferred_ip="203.0.113.1",
)


def _probe(
    status_code: int,
    *,
    text: str = "",
    location: str | None = None,
    set_cookie: str | None = None,
) -> _ProbeResponse:
    return _ProbeResponse(
        status_code=status_code,
        word_count=len(text.split()),
        byte_size=len(text),
        text=text,
        location=location,
        set_cookie=set_cookie,
    )


def _post(url: str = "https://wiki.example.com/") -> dict:
    return {
        "connector_id": str(uuid.uuid4()),
        "org_id": "42",
        "url": url,
    }


class TestCrawlKeepaliveEndpoint:
    def test_successful_touch_returns_ok_true(self) -> None:
        """Cookies decrypt and the pinned fetch returns 200 -> ok=True."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_probe(200, text="Welcome to the handbook. " * 20),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "reason": None}

    def test_root_redirect_to_language_path_then_ok(self) -> None:
        """The production failure mode: root 302s to /en, then /en is the
        real authenticated page -> the probe must follow that hop and
        report ok=True, not treat the bare 302 as a dead session."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                side_effect=[
                    _probe(302, location="/en"),
                    _probe(200, text="Welcome to the handbook. " * 20),
                ],
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "reason": None}

    def test_redirect_to_login_page_returns_ok_false_with_reason(self) -> None:
        """A same-site redirect the probe follows can still land on a login
        page (expired session) — the final page must be classified, and the
        response must carry a reason so the caller can log it."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                side_effect=[
                    _probe(302, location="/login"),
                    _probe(200, text="Please log in to continue"),
                ],
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] == "end_of_body_login_marker"

    def test_redirect_to_another_host_is_not_followed(self) -> None:
        """A redirect leaving the connector's site (e.g. an SSO host) must
        never be followed — that is an SSRF/host-pivot boundary, not just an
        auth signal."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_probe(302, location="https://accounts.example-sso.com/login"),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] is not None and body["reason"].startswith("redirect_left_domain")

    def test_connector_not_found_returns_ok_false_no_500(self) -> None:
        """load_connector_cookies raising ConnectorNotFoundError -> ok=False, no 500."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                side_effect=ConnectorNotFoundError("connector not found"),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "reason": "cookie_load_failed"}

    def test_no_saved_credentials_returns_ok_false(self) -> None:
        """Empty cookie list (connector has no saved credentials) -> ok=False."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "reason": "no_saved_credentials"}

    def test_pinned_fetch_error_returns_ok_false_no_500(self) -> None:
        """The pinned fetch raising (e.g. connection error) -> ok=False, no 500."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_cookies",
                new_callable=AsyncMock,
                return_value=[{"name": "sid", "value": "abc123"}],
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                side_effect=ConnectionError("connection refused"),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": False, "reason": "fetch_failed"}
