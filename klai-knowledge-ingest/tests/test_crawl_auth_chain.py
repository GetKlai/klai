"""``POST /ingest/v1/crawl/auth-chain``: which hosts a site's sign-in passes through."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from knowledge_ingest.routes.crawl import _ProbeResponse


def _redirect(location: str) -> _ProbeResponse:
    return _ProbeResponse(status_code=302, word_count=0, byte_size=0, text="", location=location)


_PAGE = _ProbeResponse(status_code=200, word_count=10, byte_size=100, text="login form")


def _fake_fetch(routes: dict[str, _ProbeResponse]):
    async def fetch(url: str, pin_map: dict[str, str], cookies=None) -> _ProbeResponse:
        return routes[url]

    return patch(
        "knowledge_ingest.routes.crawl_auth_chain._probe_fetch", new=AsyncMock(side_effect=fetch)
    )


async def _validated(url: str) -> SimpleNamespace:
    host = url.split("/")[2]
    return SimpleNamespace(hostname=host, preferred_ip="93.184.216.34")


def _post(client: TestClient, url: str, validate=_validated):
    with patch("knowledge_ingest.routes.crawl_auth_chain.validate_url_pinned", new=validate):
        return client.post("/ingest/v1/crawl/auth-chain", json={"url": url})


def test_two_hop_redirect_reports_the_other_host(client: TestClient) -> None:
    routes = {
        "https://app.example.com": _PAGE,
        "https://app.example.com/login": _redirect(
            "https://idp.example.com/oauth/authorize?client=1"
        ),
        "https://idp.example.com/oauth/authorize?client=1": _redirect("/oauth/login"),
        "https://idp.example.com/oauth/login": _redirect("https://app.example.com/callback"),
        "https://app.example.com/callback": _PAGE,
    }
    with _fake_fetch(routes):
        resp = _post(client, "https://app.example.com")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"hosts": ["idp.example.com"]}


def test_a_redirect_loop_stops_at_the_hop_limit(client: TestClient) -> None:
    routes = {
        "https://app.example.com": _redirect("https://idp.example.com/a"),
        "https://idp.example.com/a": _redirect("https://app.example.com"),
        "https://app.example.com/login": _PAGE,
    }
    with _fake_fetch(routes) as fetch:
        resp = _post(client, "https://app.example.com")
    assert resp.json() == {"hosts": ["idp.example.com"]}
    assert fetch.await_count == 7  # 6 hops on the base URL walk, 1 on /login


def test_a_host_that_fails_ssrf_validation_is_not_reported(client: TestClient) -> None:
    routes = {
        "https://app.example.com": _redirect("https://internal.example.com/x"),
        "https://app.example.com/login": _PAGE,
    }

    async def validate(url: str) -> SimpleNamespace:
        if "internal" in url:
            raise ValueError("private address")
        return await _validated(url)

    with _fake_fetch(routes):
        resp = _post(client, "https://app.example.com", validate=validate)
    assert resp.json() == {"hosts": []}


def test_auth_probe_does_not_replay_a_sign_in_hosts_cookies_on_the_site(client: TestClient) -> None:
    sent: list[dict[str, str] | None] = []

    async def fetch(url: str, pin_map: dict[str, str], cookies=None) -> _ProbeResponse:
        sent.append(cookies)
        return _PAGE

    cookies = [
        {"name": "site_session", "value": "v1", "domain": ".example.com"},
        {"name": "no_domain", "value": "v2"},
        {"name": "idp_session", "value": "v3", "domain": "idp.other-example.com"},
    ]
    with patch("knowledge_ingest.routes.crawl._probe_fetch", new=AsyncMock(side_effect=fetch)):
        client.post(
            "/ingest/v1/crawl/auth-probe", json={"url": "https://example.com/", "cookies": cookies}
        )
    assert {"site_session": "v1", "no_domain": "v2"} in sent
