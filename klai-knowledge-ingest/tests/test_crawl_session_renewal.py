"""A crawled site that signs in through a second host keeps its login for days.

The site this is modelled on ends its own session 24 hours after the login,
however often its 2-hour sliding cookies are renewed, and signs in again
silently through a second host with a standard OAuth code flow
(``/login`` -> ``<sign-in host>/oauth/authorize?prompt=auto`` -> ``/login/callback``).
Every earlier fix was judged on one or two nights after a manual cookie paste,
shorter than that lifetime. These tests run the whole lifetime instead,
against a two-host fake that behaves the way the real site was measured to
behave on 2026-11-12:

- every response of either host re-issues that host's session cookie with
  ``Max-Age=7200`` and an ``XSRF-TOKEN`` of the same name on both hosts;
- a content page answers 200 with a shortened article and an embedded login
  link when the session is gone, never a redirect;
- the content host's ``/login`` starts a fresh session and binds the OAuth
  ``state`` to it, so the callback only works with the cookie that ``/login``
  issued;
- the sign-in host's session slides for 2 hours and its root redirects to its
  login form once that session is gone.

Time is injected through ``time.time``, the clock the stdlib cookie jar
resolves ``Max-Age`` against, so a 72-hour run takes a fraction of a second.
"""

from __future__ import annotations

import copy
import secrets
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlencode

import httpx
import pytest
from klai_image_storage.url_guard import ValidatedURL
from structlog.testing import capture_logs

from knowledge_ingest import crawl_tasks
from knowledge_ingest.connector_cookies import CookieJar, StoredCredentials
from knowledge_ingest.routes import crawl_sync

CONTENT = "wiki.example.com"
SIGN_IN = "login.example.org"
ELSEWHERE = "elsewhere.example.net"
PAGE = f"https://{CONTENT}/nl/handbook/getting-started"
CONNECTOR = uuid.UUID("00000000-0000-4000-8000-000000000001")
ORG = "42"

T0 = datetime(2026, 11, 1, 21, 0, tzinfo=UTC).timestamp()
HOUR = 3600.0
SLIDING = 2 * HOUR
CONTENT_LOGIN_LIFETIME = 24 * HOUR

_ARTICLE = " ".join(["The handbook explains every step of the onboarding in detail."] * 40)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def time(self) -> float:
        return self.now


class FakeSite:
    """The content host and its sign-in host, sharing one clock."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        # session id -> {"seen", "login_at", "state", "redirect_to"}
        self.content_sessions: dict[str, dict[str, Any]] = {}
        # session id -> {"seen", "user"}
        self.sign_in_sessions: dict[str, dict[str, Any]] = {}
        self.codes: dict[str, str] = {}
        # Every value each host ever issued, to tell whose cookie a request carried.
        self.issued: dict[str, set[str]] = {CONTENT: set(), SIGN_IN: set()}
        # (host, path, {cookie name: value}) of every request that reached a host.
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.authorize_redirect_host: str | None = None

    # -- helpers -----------------------------------------------------------

    def _alive(self, session: dict[str, Any] | None) -> bool:
        return session is not None and self.clock.now - session["seen"] < SLIDING

    def _logged_in(self, session: dict[str, Any]) -> bool:
        login_at = session.get("login_at")
        return login_at is not None and self.clock.now - login_at < CONTENT_LOGIN_LIFETIME

    def _set_cookies(self, host: str, name: str, sid: str) -> list[tuple[str, str]]:
        xsrf = secrets.token_hex(8)
        self.issued[host] |= {sid, xsrf}
        return [
            ("set-cookie", f"XSRF-TOKEN={xsrf}; Max-Age=7200; Path=/; Secure; SameSite=Lax"),
            ("set-cookie", f"{name}={sid}; Max-Age=7200; Path=/; Secure; HttpOnly; SameSite=Lax"),
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        cookies = dict(
            part.strip().split("=", 1)
            for part in request.headers.get("cookie", "").split(";")
            if "=" in part
        )
        self.requests.append((host, request.url.path, cookies))
        if host == CONTENT:
            return self._content(request, cookies)
        if host == SIGN_IN:
            return self._sign_in(request, cookies)
        return httpx.Response(200, text="<p>Somewhere else entirely.</p>")

    # -- content host --------------------------------------------------------

    def _content_session(self, cookies: dict[str, str]) -> tuple[str, dict[str, Any]]:
        sid = cookies.get("wiki_session", "")
        session = self.content_sessions.get(sid)
        if self._alive(session):
            assert session is not None
            session["seen"] = self.clock.now
            return sid, session
        sid = secrets.token_hex(8)
        session = {"seen": self.clock.now, "login_at": None, "state": None, "redirect_to": None}
        self.content_sessions[sid] = session
        return sid, session

    def _content(self, request: httpx.Request, cookies: dict[str, str]) -> httpx.Response:
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        if path == "/login":
            # A fresh session per sign-in attempt, with the OAuth state bound to it.
            sid = secrets.token_hex(8)
            state = secrets.token_hex(8)
            self.content_sessions[sid] = {
                "seen": self.clock.now,
                "login_at": None,
                "state": state,
                "redirect_to": query.get("redirect_to", ["/"])[0],
            }
            authorize = f"https://{SIGN_IN}/oauth/authorize?" + urlencode(
                {
                    "prompt": "auto",
                    "client_id": "wiki",
                    "state": state,
                    "response_type": "code",
                    "redirect_uri": f"https://{CONTENT}/login/callback",
                }
            )
            return httpx.Response(
                302,
                headers=[("location", authorize), *self._set_cookies(CONTENT, "wiki_session", sid)],
            )
        sid, session = self._content_session(cookies)
        if path == "/login/callback":
            state = query.get("state", [""])[0]
            code = query.get("code", [""])[0]
            if session.get("state") != state or self.codes.pop(code, None) != state:
                return httpx.Response(403, text="<p>This sign-in link is invalid.</p>")
            # Signing in rotates the session id.
            new_sid = secrets.token_hex(8)
            self.content_sessions[new_sid] = {
                "seen": self.clock.now,
                "login_at": self.clock.now,
                "state": None,
                "redirect_to": None,
            }
            del self.content_sessions[sid]
            return httpx.Response(
                302,
                headers=[
                    ("location", session["redirect_to"] or "/"),
                    *self._set_cookies(CONTENT, "wiki_session", new_sid),
                ],
            )
        headers = self._set_cookies(CONTENT, "wiki_session", sid)
        if self._logged_in(session):
            return httpx.Response(
                200, headers=headers, text=f"<article><p>{_ARTICLE}</p></article>"
            )
        gate = (
            "<article><p>The handbook explains the first step.</p>"
            f'<p><a href="/login?redirect_to=https://{CONTENT}{path}">Log in</a>'
            " to read this full article.</p></article>"
        )
        return httpx.Response(200, headers=headers, text=gate)

    # -- sign-in host ----------------------------------------------------------

    def _sign_in(self, request: httpx.Request, cookies: dict[str, str]) -> httpx.Response:
        sid = cookies.get("sso_session", "")
        session = self.sign_in_sessions.get(sid)
        if self._alive(session):
            assert session is not None
            session["seen"] = self.clock.now
        else:
            sid = secrets.token_hex(8)
            session = {"seen": self.clock.now, "user": False}
            self.sign_in_sessions[sid] = session
        headers = self._set_cookies(SIGN_IN, "sso_session", sid)
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        if path == "/login":
            form = (
                '<form method="post" action="/login"><input type="hidden" name="_token">'
                '<input type="email" name="email"><input type="password" name="password"></form>'
            )
            return httpx.Response(200, headers=headers, text=form)
        if not session["user"]:
            return httpx.Response(302, headers=[("location", f"https://{SIGN_IN}/login"), *headers])
        if path == "/":
            return httpx.Response(302, headers=[("location", f"https://{SIGN_IN}/home"), *headers])
        if path == "/home":
            return httpx.Response(200, headers=headers, text="<h1>Your applications</h1>")
        if path == "/oauth/authorize":
            if self.authorize_redirect_host:
                target = f"https://{self.authorize_redirect_host}/callback"
                return httpx.Response(302, headers=[("location", target), *headers])
            code = secrets.token_hex(8)
            self.codes[code] = query["state"][0]
            target = (
                query["redirect_uri"][0]
                + "?"
                + urlencode({"code": code, "state": query["state"][0]})
            )
            return httpx.Response(302, headers=[("location", target), *headers])
        return httpx.Response(404, headers=headers)

    # -- the person who logs in once ------------------------------------------

    def browser_login(self) -> list[dict[str, Any]]:
        """Sign in on both hosts the way a person does, and export the cookies."""
        expires = datetime.fromtimestamp(self.clock.now + SLIDING, UTC).isoformat()
        wiki_sid = secrets.token_hex(8)
        self.content_sessions[wiki_sid] = {
            "seen": self.clock.now,
            "login_at": self.clock.now,
            "state": None,
            "redirect_to": None,
        }
        sso_sid = secrets.token_hex(8)
        self.sign_in_sessions[sso_sid] = {"seen": self.clock.now, "user": True}
        self.issued[CONTENT].add(wiki_sid)
        self.issued[SIGN_IN].add(sso_sid)
        return [
            {
                "name": "wiki_session",
                "value": wiki_sid,
                "domain": CONTENT,
                "path": "/",
                "expires": expires,
            },
            {
                "name": "sso_session",
                "value": sso_sid,
                "domain": SIGN_IN,
                "path": "/",
                "expires": expires,
            },
        ]


class Vault:
    """The connector's stored credentials, with the compare-and-swap of the real store."""

    def __init__(self, cookies: list[dict[str, Any]]) -> None:
        self.payload: dict[str, Any] = {"cookies": copy.deepcopy(cookies)}
        self.version = 0

    async def load(self, **_kwargs: Any) -> StoredCredentials:
        return StoredCredentials(
            payload=copy.deepcopy(self.payload),
            encrypted=str(self.version).encode(),
            dek_enc=b"dek",
            org_id=7,
        )

    async def store(self, *, stored: StoredCredentials, jar: CookieJar, **_kwargs: Any) -> bool:
        if stored.encrypted != str(self.version).encode():
            return False
        self.payload = {**stored.payload, "cookies": copy.deepcopy(jar.cookies)}
        self.version += 1
        return True

    def value(self, name: str) -> str:
        return next(c["value"] for c in self.payload["cookies"] if c["name"] == name)


async def _validate(url: str) -> ValidatedURL:
    host = httpx.URL(url).host
    return ValidatedURL(
        url=url, hostname=host, pinned_ips=frozenset({"203.0.113.9"}), preferred_ip="203.0.113.9"
    )


@contextmanager
def _world(site: FakeSite, clock: Clock, vault: Vault | None = None):
    transport = httpx.MockTransport(site.handle)
    with ExitStack() as stack:
        stack.enter_context(patch("time.time", clock.time))
        stack.enter_context(
            patch(
                "knowledge_ingest.routes.crawl.PinnedResolverTransport", lambda _pin_map: transport
            )
        )
        stack.enter_context(
            patch("knowledge_ingest.routes.crawl_sync.validate_url_pinned", _validate)
        )
        stack.enter_context(patch("knowledge_ingest.routes.crawl.validate_url_pinned", _validate))
        stack.enter_context(
            patch(
                "knowledge_ingest.routes.crawl_sync.get_pool", AsyncMock(return_value=MagicMock())
            )
        )
        if vault is not None:
            stack.enter_context(
                patch("knowledge_ingest.routes.crawl_sync.load_connector_credentials", vault.load)
            )
            stack.enter_context(
                patch("knowledge_ingest.routes.crawl_sync.store_connector_cookie_jar", vault.store)
            )
        crawl_sync._last_probe_ok.clear()
        yield


async def _tick(hosts: list[str] | None = None) -> crawl_sync.CrawlKeepAliveResponse:
    return await crawl_sync.keep_session(
        connector_id=CONNECTOR,
        org_id=ORG,
        url=PAGE,
        auth_chain_hosts=[SIGN_IN] if hosts is None else hosts,
    )


class _JobRows:
    """Stands in for the tenant-scoped connection the pre-flight fails a job on."""

    def __init__(self) -> None:
        self.failed: list[tuple[str, str]] = []

    @contextmanager
    def _noop(self):
        yield

    def factory(self, _org_id: str):
        rows = self

        class _Conn:
            async def execute(self, _sql: str, job_id: str, error: str) -> str:
                rows.failed.append((job_id, error))
                return "UPDATE 1"

        class _Ctx:
            async def __aenter__(self) -> _Conn:
                return _Conn()

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Ctx()


async def _preflight(rows: _JobRows) -> bool:
    with patch.object(crawl_tasks, "_crawl_db_connection", rows.factory):
        return await crawl_tasks.preflight_session(
            job_id="job-1",
            org_id=ORG,
            connector_id=str(CONNECTOR),
            url=PAGE,
            auth_chain_hosts=[SIGN_IN],
        )


def _events(logs: list[Any], name: str) -> list[Any]:
    return [entry for entry in logs if entry["event"] == name]


@pytest.mark.asyncio
async def test_seventy_two_hours_of_keepalive_keep_every_crawl_logged_in() -> None:
    clock = Clock()
    site = FakeSite(clock)
    vault = Vault(site.browser_login())
    first = {"wiki": vault.value("wiki_session"), "sso": vault.value("sso_session")}
    rows = _JobRows()
    crawl_at = {round(h * HOUR) for h in (5 + 40 / 60, 29 + 40 / 60, 53 + 40 / 60)}

    with _world(site, clock, vault), capture_logs() as logs:
        preflights: list[bool] = []
        for step in range(72 * 2 + 1):
            elapsed = step * 1800
            for at in sorted(crawl_at):
                if elapsed - 1800 < at <= elapsed:
                    clock.now = T0 + at
                    preflights.append(await _preflight(rows))
            clock.now = T0 + elapsed
            await _tick()

    assert preflights == [True, True, True]
    assert rows.failed == []
    reminted = _events(logs, "crawl_keepalive_session_reminted")
    assert len(reminted) >= 2
    assert all(event["connector_id"] == str(CONNECTOR) for event in reminted)
    assert _events(logs, "crawl_keepalive_session_logged_out") == []

    wiki = vault.value("wiki_session")
    sso = vault.value("sso_session")
    assert wiki != first["wiki"]
    assert site._logged_in(site.content_sessions[wiki])
    assert site._alive(site.sign_in_sessions[sso]) and site.sign_in_sessions[sso]["user"]
    sso_cookie = next(c for c in vault.payload["cookies"] if c["name"] == "sso_session")
    assert datetime.fromisoformat(sso_cookie["expires"]).timestamp() == clock.now + SLIDING

    # Host isolation over the whole run: every cookie a host received is one
    # that host issued, so no request ever carried the other host's cookie.
    assert site.requests
    for host, _path, sent in site.requests:
        assert set(sent.values()) <= site.issued[host], (host, sorted(sent))


@pytest.mark.asyncio
async def test_a_crawl_after_a_missed_keepalive_signs_in_again_before_it_starts() -> None:
    clock = Clock()
    site = FakeSite(clock)
    vault = Vault(site.browser_login())
    rows = _JobRows()
    with _world(site, clock, vault), capture_logs() as logs:
        # The keep-alive ran until an hour before the content login lapsed,
        # then stopped; the sign-in host's session is still alive.
        for step in range(47):
            clock.now = T0 + step * 1800
            await _tick()
        clock.now = T0 + 24 * HOUR + 600
        may_crawl = await _preflight(rows)

    assert may_crawl is True
    assert rows.failed == []
    assert len(_events(logs, "crawl_keepalive_session_reminted")) == 1
    assert site._logged_in(site.content_sessions[vault.value("wiki_session")])


@pytest.mark.asyncio
async def test_a_lapsed_sign_in_session_asks_for_one_new_login_and_keeps_the_cookies() -> None:
    clock = Clock()
    site = FakeSite(clock)
    vault = Vault(site.browser_login())
    rows = _JobRows()
    with _world(site, clock, vault), capture_logs() as logs:
        for step in range(48):
            if step == 46:
                # The sign-in host ends the person's session (a logout or a
                # password change); its cookies stay valid as cookies.
                for session in site.sign_in_sessions.values():
                    session["user"] = False
            clock.now = T0 + step * 1800
            await _tick()
        stored_before = copy.deepcopy(vault.payload)
        walks_before = len([r for r in site.requests if r[0] == CONTENT and r[1] == "/login"])
        clock.now = T0 + 24 * HOUR
        first = await _tick()
        second = await _tick()
        may_crawl = await _preflight(rows)

    assert (first.ok, first.login_required) == (False, True)
    assert first.reason == f"login_required:sign_in_required_at:{SIGN_IN}"
    assert second.login_required is True
    logged_out = _events(logs, "crawl_keepalive_session_logged_out")
    assert len(logged_out) == 1
    assert logged_out[0]["log_level"] == "error"
    assert logged_out[0]["reason"] == first.reason
    # One walk per call (two ticks and one pre-flight), never a retry inside one.
    content_logins = [r for r in site.requests if r[0] == CONTENT and r[1] == "/login"]
    assert len(content_logins) - walks_before == 3
    assert vault.payload == stored_before
    assert may_crawl is False
    assert rows.failed == [("job-1", first.reason)]


@pytest.mark.asyncio
async def test_a_redirect_off_the_allowed_hosts_aborts_the_walk_without_contacting_that_host() -> (
    None
):
    clock = Clock()
    site = FakeSite(clock)
    vault = Vault(site.browser_login())
    site.authorize_redirect_host = ELSEWHERE
    with _world(site, clock, vault):
        for step in range(48):
            clock.now = T0 + step * 1800
            await _tick()
        clock.now = T0 + 24 * HOUR
        result = await _tick()

    assert result.login_required is True
    assert result.reason == f"login_required:redirect_left_host:{ELSEWHERE}"
    assert all(host != ELSEWHERE for host, _path, _sent in site.requests)
    for host, _path, sent in site.requests:
        assert set(sent.values()) <= site.issued[host], (host, sorted(sent))


@pytest.mark.asyncio
async def test_the_walk_fails_when_the_login_entry_cookie_does_not_reach_the_callback() -> None:
    """Pins the bug class that makes a real site reject the callback: the
    OAuth state is bound to the session cookie ``/login`` issued."""
    clock = Clock()
    site = FakeSite(clock)
    vault = Vault(site.browser_login())
    real_merge = CookieJar.merge_set_cookies

    def merge_except_login_entry(self: CookieJar, url: str, headers: list[str]) -> None:
        if httpx.URL(url).path != "/login" or httpx.URL(url).host != CONTENT:
            real_merge(self, url, headers)

    with _world(site, clock, vault):
        for step in range(48):
            clock.now = T0 + step * 1800
            await _tick()
        clock.now = T0 + 24 * HOUR
        with patch.object(CookieJar, "merge_set_cookies", merge_except_login_entry):
            result = await _tick()

    assert result.login_required is True
    assert result.reason == "login_required:callback_rejected:403"


def _auth_probe(client: Any, cookies: list[dict[str, Any]]) -> dict[str, Any]:
    with patch("knowledge_ingest.routes.crawl.crawl_dom_summary", AsyncMock(return_value=None)):
        resp = client.post(
            "/ingest/v1/crawl/auth-probe",
            json={"url": PAGE, "cookies": cookies, "auth_chain_hosts": [SIGN_IN]},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_testing_cookies_for_both_hosts_proves_on_the_site_that_klai_can_sign_in_again(
    client: Any,
) -> None:
    clock = Clock()
    clock.now = time.time()
    site = FakeSite(clock)
    cookies = site.browser_login()
    with _world(site, clock):
        body = _auth_probe(client, cookies)
    assert body["classification"] == "auth_ok"
    assert body["renewal"] == {"can_renew": True, "reason": None}
    # The proof ran without the content host's own session cookie: a live
    # session there would hide whether the sign-in host can mint a new one.
    callback = [r for r in site.requests if r[1] == "/login/callback"]
    assert callback and set(callback[0][2].values()) <= site.issued[CONTENT]
    assert cookies[0]["value"] not in callback[0][2].values()


def test_testing_only_the_sites_own_cookies_says_klai_cannot_sign_in_again_and_why(
    client: Any,
) -> None:
    clock = Clock()
    clock.now = time.time()
    site = FakeSite(clock)
    cookies = site.browser_login()
    with _world(site, clock):
        body = _auth_probe(client, [c for c in cookies if c["domain"] == CONTENT])
    assert body["classification"] == "auth_ok"
    assert body["renewal"] == {"can_renew": False, "reason": f"no_cookies_for:{SIGN_IN}"}
    assert all(host != SIGN_IN for host, _path, _sent in site.requests)


@pytest.mark.asyncio
async def test_a_crawl_whose_login_cannot_be_renewed_fetches_nothing() -> None:
    class _App:
        run_crawl: Any = None

        def task(self, **_kwargs: Any):
            def _register(fn):
                self.run_crawl = fn
                return fn

            return _register

    app = _App()
    crawl_tasks.register_crawl_tasks(app)
    rows = _JobRows()
    run_job = AsyncMock()
    verdict = crawl_sync.CrawlKeepAliveResponse(
        ok=False, reason="login_required:no_cookies_for:login.example.org", login_required=True
    )
    with (
        patch.object(crawl_tasks, "_crawl_db_connection", rows.factory),
        patch(
            "knowledge_ingest.routes.crawl_sync.keep_session", AsyncMock(return_value=verdict)
        ) as check,
        patch("knowledge_ingest.adapters.crawler.run_crawl_job", run_job),
    ):
        await app.run_crawl(
            job_id="job-1",
            org_id=ORG,
            kb_slug="kb",
            start_url=f"https://{CONTENT}/nl/",
            max_depth=2,
            connector_id=str(CONNECTOR),
            auth_chain_hosts=[SIGN_IN],
        )
    assert check.await_args is not None
    assert check.await_args.kwargs["url"] == f"https://{CONTENT}/nl/"
    assert check.await_args.kwargs["auth_chain_hosts"] == [SIGN_IN]
    run_job.assert_not_awaited()
    assert rows.failed == [("job-1", verdict.reason)]
