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
following it. Production data (September 2026) showed the probed URL is
usually the site root, and many CMSes 302 that to a language path (``/en``)
even for an authenticated session — every tick reported ok=false and the
keep-alive never actually kept anything alive. These tests pin the fix: the
probe now follows same-host https redirects and classifies the final page
with the shared ``classify_auth_wall`` heuristic instead of trusting a bare
status code.

2026-09-25 (review pass): the first version of this fix allowed apex/www and
scheme-downgrading redirects to be followed with the same cookie jar (a real
cookie-leak/downgrade risk), never told the classifier about a followed
redirect's target, fed it link-stripped text, and re-logged an error every
tick for a connector that stayed logged out. The tests below pin all four.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from klai_image_storage.url_guard import ValidatedURL

from knowledge_ingest.connector_cookies import ConnectorNotFoundError, StoredCredentials
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
    new_cookies: dict[tuple[str, str], str] | None = None,
) -> _ProbeResponse:
    return _ProbeResponse(
        status_code=status_code,
        word_count=len(text.split()),
        byte_size=len(text),
        text=text,
        location=location,
        set_cookie=set_cookie,
        new_cookies=new_cookies or {},
    )


def _creds(cookies: list[dict]) -> StoredCredentials:
    return StoredCredentials(
        payload={"cookies": cookies},
        encrypted=b"blob-read-by-this-probe",
        dek_enc=b"dek",
        org_id=7,
    )


def _post(connector_id: str | None = None, url: str = "https://wiki.example.com/") -> dict:
    return {
        "connector_id": connector_id or str(uuid.uuid4()),
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
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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
        """The production failure mode: root 302s to /en on the same host,
        then /en is the real authenticated page -> the probe must follow
        that hop and report ok=True, not treat the bare 302 as a dead
        session."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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
                    _probe(302, location="https://wiki.example.com/en"),
                    _probe(200, text="Welcome to the handbook. " * 20),
                ],
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "reason": None}

    def test_redirect_to_login_page_returns_ok_false_with_reason(self) -> None:
        """A same-host redirect the probe follows can still land on a login
        page (expired session) — the final page must be classified, and the
        response must carry a reason so the caller can log it."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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
                    _probe(302, location="https://wiki.example.com/login"),
                    _probe(200, text="Please log in to continue"),
                ],
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        # Both the redirect target ("/login") and the landed page's own body
        # ("please log in to continue") are independently walled signals.
        assert body["reason"] == "redirect_to_login, end_of_body_login_marker"

    def test_redirect_to_another_host_is_not_followed(self) -> None:
        """A redirect leaving the connector's host (e.g. an SSO host) must
        never be followed — that is an SSRF/host-pivot boundary, not just an
        auth signal."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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
        assert body["reason"] is not None and body["reason"].startswith("redirect_left_host")

    def test_https_to_http_downgrade_is_not_followed(self) -> None:
        """A same-host redirect from https to http must not be followed --
        following it would replay the connector's saved cookies over plain
        HTTP."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_probe(302, location="http://wiki.example.com/en"),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] is not None and body["reason"].startswith("redirect_left_host")

    def test_apex_www_redirect_is_not_followed(self) -> None:
        """A redirect to the www-variant of the same site must not be
        followed -- the probe's cookies are scoped to the exact configured
        host, and treating apex/www as interchangeable would replay a
        host-scoped cookie onto a different host."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_probe(302, location="https://www.wiki.example.com/en"),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] is not None and body["reason"].startswith("redirect_left_host")

    def test_followed_redirect_target_feeds_the_classifier(self) -> None:
        """A followed redirect landing on a /login path must be caught via
        classify_auth_wall's redirect_to_login rule even when the login
        page's own body is long enough to dodge every body-based rule --
        the redirect target itself has to reach the classifier."""
        pool = _make_pool()
        # Long, innocuous body: no login phrases, no password form, no thin
        # body -- every OTHER classify_auth_wall rule stays silent. Only the
        # redirect_target_url rule can catch this.
        long_body = "This is a perfectly ordinary paragraph of text. " * 40
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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
                    _probe(302, location="https://wiki.example.com/login?return_to=/en"),
                    _probe(200, text=long_body),
                ],
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] == "redirect_to_login"

    def test_embedded_login_gate_needs_link_href_preserved(self) -> None:
        """embedded_login_gate matches markdown link syntax
        (``[..login..](..login..)``); a plain-text view that drops the href
        can never trigger it, silently losing detection of a login gate
        embedded in an otherwise long page."""
        pool = _make_pool()
        html = (
            "<html><body><article>"
            + "Ordinary paragraph text about the product. " * 30
            + '<p><a href="https://wiki.example.com/login">Log in</a> to '
            "view this article in full.</p>" + "</article></body></html>"
        )
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_probe(200, text=html),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] == "embedded_login_gate"

    def test_footer_boilerplate_does_not_trip_end_of_body_marker(self) -> None:
        """A sitewide footer link ("please log in") must not make every page
        on the site look logged-out -- the classifier's end-of-body window
        must see the article's own tail, not trailing nav/footer chrome."""
        pool = _make_pool()
        html = (
            "<html><body>"
            "<article>" + "Ordinary paragraph text about the product. " * 30 + "</article>"
            "<footer>Please log in for account settings.</footer>"
            "</body></html>"
        )
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                return_value=_probe(200, text=html),
            ),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "reason": None}

    def test_allows_five_redirects_then_the_final_request(self) -> None:
        """Exactly 5 followed same-host redirects plus a final 200 must
        succeed -- the hop budget must not be consumed one request short."""
        pool = _make_pool()
        responses = [_probe(302, location=f"https://wiki.example.com/{i}") for i in range(5)]
        responses.append(_probe(200, text="Welcome back. " * 20))
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                side_effect=responses,
            ) as probe_mock,
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.json() == {"ok": True, "reason": None}
        assert probe_mock.await_count == 6

    def test_more_than_five_redirects_fails(self) -> None:
        """A 6th consecutive same-host redirect exceeds the hop budget."""
        pool = _make_pool()
        responses = [_probe(302, location=f"https://wiki.example.com/{i}") for i in range(6)]
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync._probe_fetch",
                new_callable=AsyncMock,
                side_effect=responses,
            ) as probe_mock,
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        body = resp.json()
        assert body["ok"] is False
        assert body["reason"] == "too_many_redirects"
        assert probe_mock.await_count == 6

    def test_repeated_failure_logs_error_once_not_every_tick(self) -> None:
        """A connector stuck logged-out must not re-alert on every 30-minute
        tick forever -- one error on the failing transition, nothing while
        it stays failing. (structlog in this codebase only routes through
        stdlib logging -- and hence pytest's caplog -- once
        knowledge_ingest.app has been imported somewhere in the test run, so
        this asserts against a patched logger directly, same pattern as
        test_auth_wall_signal_reporting.py.)"""
        pool = _make_pool()
        connector_id = str(uuid.uuid4())
        mock_logger = MagicMock()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch("knowledge_ingest.routes.crawl_sync.logger", mock_logger),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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
            client.post("/ingest/v1/crawl/keep-alive", json=_post(connector_id))
            client.post("/ingest/v1/crawl/keep-alive", json=_post(connector_id))
            client.post("/ingest/v1/crawl/keep-alive", json=_post(connector_id))

        logged_out_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and call.args[0] == "crawl_keepalive_session_logged_out"
        ]
        assert len(logged_out_calls) == 1

    def test_connector_not_found_returns_ok_false_no_500(self) -> None:
        """load_connector_cookies raising ConnectorNotFoundError -> ok=False, no 500."""
        pool = _make_pool()
        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
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
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=None,
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
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=_creds([{"name": "sid", "value": "abc123"}]),
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


class TestKeepaliveStoresRefreshedSessionCookies:
    """2026-09-26: the probe kept a wiki session touched every 30 minutes, yet
    the login was still gone within a day. The site answers every request with
    a fresh session cookie, and the probe threw it away, so the stored jar kept
    replaying the value from the day it was pasted. A browser keeps the newest
    value; the probe must too, or a site that rotates its session id logs the
    connector out no matter how often it is touched.

    Review 2026-09-26: a 200 page without a login marker is not proof the
    stored session mattered (a public page sets an anonymous session cookie
    too), so the same URL is fetched once without cookies and the write only
    happens when that anonymous answer is walled. The write is also bound to
    the blob this probe read, so a paste made meanwhile wins."""

    _ARTICLE = "Welcome to the handbook. " * 20
    _GATE = (
        "<main><p>Intro text for the article.</p>"
        '<h2><a href="https://wiki.example.com/login?redirect_to=/a">Log in</a>'
        " when you want to read this article</h2></main>"
    )

    def _run(self, *, with_cookies: _ProbeResponse, anonymous: _ProbeResponse):
        pool = _make_pool()
        store = AsyncMock(return_value=1)
        stored = _creds([{"name": "sid", "value": "pasted-value"}])
        fetch_calls: list[dict | None] = []

        async def _fetch(url: str, pin_map: dict, cookies: dict | None = None) -> _ProbeResponse:
            fetch_calls.append(dict(cookies) if cookies else None)
            return with_cookies if cookies else anonymous

        with (
            _client_with_patches(pool) as (client, _defer),
            patch(
                "knowledge_ingest.routes.crawl_sync.load_connector_credentials",
                new_callable=AsyncMock,
                return_value=stored,
            ),
            patch(
                "knowledge_ingest.routes.crawl_sync.validate_url_pinned",
                new_callable=AsyncMock,
                return_value=_VALIDATED,
            ),
            patch("knowledge_ingest.routes.crawl_sync._probe_fetch", side_effect=_fetch),
            patch("knowledge_ingest.routes.crawl_sync.store_refreshed_connector_cookies", store),
        ):
            resp = client.post("/ingest/v1/crawl/keep-alive", json=_post())
        assert resp.status_code == 200
        return resp.json(), store, stored, fetch_calls

    def test_session_that_unlocks_a_gated_page_stores_the_reissued_cookie(self) -> None:
        body, store, stored, fetch_calls = self._run(
            with_cookies=_probe(200, text=self._ARTICLE, new_cookies={("sid", "/"): "issued"}),
            anonymous=_probe(200, text=self._GATE),
        )
        assert body == {"ok": True, "reason": None}
        assert fetch_calls == [{"sid": "pasted-value"}, None]
        store.assert_awaited_once()
        kwargs = store.await_args.kwargs
        assert kwargs["stored"] is stored
        assert kwargs["hostname"] == "wiki.example.com"
        assert kwargs["refreshed"] == {("sid", "/"): "issued"}

    def test_public_page_never_overwrites_the_stored_session(self) -> None:
        body, store, _stored, _calls = self._run(
            with_cookies=_probe(200, text=self._ARTICLE, new_cookies={("sid", "/"): "anonymous"}),
            anonymous=_probe(200, text=self._ARTICLE),
        )
        assert body == {"ok": True, "reason": None}
        store.assert_not_awaited()

    def test_logged_out_probe_never_overwrites_the_stored_session(self) -> None:
        body, store, _stored, _calls = self._run(
            with_cookies=_probe(200, text=self._GATE, new_cookies={("sid", "/"): "anonymous"}),
            anonymous=_probe(200, text=self._GATE),
        )
        assert body["ok"] is False
        store.assert_not_awaited()
