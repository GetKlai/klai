"""SPEC-SEC-HYGIENE-001 HY-31 — /api/v1/compute-fingerprint regression tests.

Pre-fix state: ``routes/fingerprint.py`` lazy-imported
``app.adapters.webcrawler`` (deleted by SPEC-CRAWLER-004 Fase F, commit
2295bc0c). Every request 502'd at module-import time with a body that
leaked ``ModuleNotFoundError: No module named 'app.adapters.webcrawler'``
— an internal-path disclosure (REQ-31.2 violation) and a silently-broken
admin feature: portal's ``_auto_fill_canary_fingerprint`` swallowed the
exception and disabled the canary, so connectors saved without canary
protection (REQ-31.1).

Post-fix state (Branch B chosen — feature is in active use, broken in
prod): the endpoint is rewired to the shared crawl4ai HTTP client at
``settings.crawl4ai_api_url`` (mirror of
``knowledge_ingest.crawl4ai_client.crawl_page``). The 502 ``detail``
becomes the generic string ``"Crawl failed"``; the original exception
goes to ``logger.exception`` only.

These tests pin the four-quadrant contract from AC-31 Branch B:

| Crawl outcome                | Expected response                                   |
|------------------------------|-----------------------------------------------------|
| Returns >=20 words markdown  | 200 + ``{fingerprint: <hex>, word_count: int}``     |
| Returns <20 words markdown   | 422 (cannot fingerprint)                            |
| Crawler raises any exception | 502 with detail exactly ``"Crawl failed"``          |
| Any 502                      | Body contains no internal module names / paths      |
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.fingerprint import router as fingerprint_router

_PORTAL_TEST_URL = "https://example.test/article"
_LONG_MARKDOWN = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua enim ad minim veniam quis nostrud "
    "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat duis aute "
    "irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat."
)


def _build_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a FastAPI client with the fingerprint router mounted.

    Bypass ``_require_portal_call`` (used inline, not via Depends) so the
    test does not need to set up the portal-secret middleware path.
    """
    app = FastAPI()
    app.include_router(fingerprint_router, prefix="/api/v1")

    monkeypatch.setattr(
        "app.routes.fingerprint._require_portal_call", lambda _request: None
    )
    # Stub Settings so the route does not blow up on missing required
    # env vars (database_url, zitadel_*) when instantiated inside the
    # request handler. The crawl4ai_* fields are the only ones the
    # rewired endpoint reads.
    monkeypatch.setattr(
        "app.routes.fingerprint.Settings",
        lambda: SimpleNamespace(
            crawl4ai_api_url="http://crawl4ai.test:11235",
            crawl4ai_internal_key="",
        ),
    )
    return TestClient(app, raise_server_exceptions=False)


def _patch_crawl(
    monkeypatch: pytest.MonkeyPatch,
    *,
    markdown: str | None = None,
    raises: Exception | None = None,
) -> None:
    """Patch the crawl4ai fetch helper.

    raising=False keeps pre-fix runs viable: when the helper does not yet
    exist on the module (RED state), the patch is a no-op, the route
    falls through to the broken ``app.adapters.webcrawler`` lazy import,
    and the assertions surface the original failure.
    """

    async def _fake_fetch(_url: str, _cookies: Any, _settings: Any) -> str:
        if raises is not None:
            raise raises
        return markdown or ""

    monkeypatch.setattr(
        "app.routes.fingerprint._fetch_page_markdown", _fake_fetch, raising=False
    )


# ---------------------------------------------------------------------------
# REQ-31.1 / REQ-31.3 — happy path, fingerprint computed
# ---------------------------------------------------------------------------


def test_returns_200_with_fingerprint_and_word_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _build_client(monkeypatch)
    _patch_crawl(monkeypatch, markdown=_LONG_MARKDOWN)

    response = client.post(
        "/api/v1/compute-fingerprint",
        json={"url": _PORTAL_TEST_URL},
    )

    assert response.status_code == 200, (
        f"expected 200, got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert "fingerprint" in body and isinstance(body["fingerprint"], str)
    # 16 hex chars = 64-bit SimHash per content_fingerprint.compute_content_fingerprint.
    assert len(body["fingerprint"]) == 16
    assert int(body["fingerprint"], 16) >= 0  # is valid hex
    assert body["word_count"] >= 20


# ---------------------------------------------------------------------------
# REQ-31.3 — short page returns 422
# ---------------------------------------------------------------------------


def test_returns_422_on_too_few_words(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_client(monkeypatch)
    _patch_crawl(monkeypatch, markdown="just a few words here")

    response = client.post(
        "/api/v1/compute-fingerprint",
        json={"url": _PORTAL_TEST_URL},
    )

    assert response.status_code == 422, (
        f"expected 422, got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# REQ-31.2 — generic 502 with no internal-detail leakage
# ---------------------------------------------------------------------------


def test_returns_502_with_generic_detail_on_crawl_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any crawl exception maps to a generic 502 body."""
    client = _build_client(monkeypatch)
    _patch_crawl(
        monkeypatch,
        raises=httpx.ConnectError(
            "http://crawl4ai:11235/crawl: connection refused"
        ),
    )

    response = client.post(
        "/api/v1/compute-fingerprint",
        json={"url": _PORTAL_TEST_URL},
    )

    assert response.status_code == 502, (
        f"expected 502, got {response.status_code}: {response.text}"
    )
    assert response.json() == {"detail": "Crawl failed"}


@pytest.mark.parametrize(
    "leak_substring",
    [
        "app.adapters.webcrawler",  # original HY-31 leak
        "ModuleNotFoundError",
        "ConnectError",
        "11235",  # crawl4ai port
        "crawl4ai:",  # internal hostname
        "httpx",  # exception class noise
        "Traceback",
    ],
)
def test_502_body_does_not_leak_internal_topology(
    monkeypatch: pytest.MonkeyPatch,
    leak_substring: str,
) -> None:
    """REQ-31.2: 502 response body never carries internal module names,
    hostnames, or exception class names.
    """
    client = _build_client(monkeypatch)
    _patch_crawl(
        monkeypatch,
        raises=RuntimeError(
            "ModuleNotFoundError app.adapters.webcrawler ConnectError "
            "httpx://crawl4ai:11235 Traceback (most recent call last)"
        ),
    )

    response = client.post(
        "/api/v1/compute-fingerprint",
        json={"url": _PORTAL_TEST_URL},
    )

    assert response.status_code == 502
    assert leak_substring not in response.text, (
        f"REQ-31.2 violation: 502 body leaked {leak_substring!r}: {response.text!r}"
    )


# ---------------------------------------------------------------------------
# Defense-in-depth source-text guard — the deleted import must never re-appear
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HTTP-level integration — exercise the real httpx + crawl4ai response shape
# ---------------------------------------------------------------------------
#
# The other tests in this file patch ``_fetch_page_markdown`` directly,
# which is great for asserting the route's status-code/leak contract but
# leaves a gap: a future schema change in crawl4ai's POST /crawl response
# would not be caught (because the helper never runs in those tests).
#
# This test stops one level lower — it replaces ``httpx.AsyncClient`` in
# the fingerprint module's namespace with a fake that records the request
# shape and returns a canned response in the actual format crawl4ai 0.8.x
# emits today. Coverage:
#   - ``_build_crawl_payload`` produces a payload crawl4ai will accept.
#   - The POST is made to ``{crawl4ai_api_url}/crawl`` (path correct).
#   - The Bearer header is sent iff ``crawl4ai_internal_key`` is non-empty.
#   - ``_extract_markdown`` handles the dict-shaped ``markdown`` field.
#   - End-to-end: response feeds into ``compute_content_fingerprint`` and
#     produces a valid 16-hex-char SimHash.


class _FakeResponse:
    """Stub of ``httpx.Response`` — only the methods the helper calls."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeAsyncClient:
    """Records POST calls; returns a canned response. Async-context-manager
    just like ``httpx.AsyncClient``.
    """

    def __init__(
        self,
        *,
        response: _FakeResponse | None = None,
        raises: Exception | None = None,
        **_kwargs: Any,
    ) -> None:
        self.response = response
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any] | None, dict[str, str] | None]] = []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append((url, json, headers))
        if self.raises is not None:
            raise self.raises
        assert self.response is not None
        return self.response


def _crawl4ai_single_page_response(url: str, markdown: str) -> dict[str, Any]:
    """Build a response in the exact shape crawl4ai 0.8.x's POST /crawl emits.

    Mirror of the ``data.get("results", [])`` branch in
    ``knowledge_ingest.crawl4ai_client._extract_result``.
    """
    return {
        "results": [
            {
                "url": url,
                "markdown": {
                    "fit_markdown": markdown,
                    "raw_markdown": markdown,
                },
                "html": "<html><body><p>...</p></body></html>",
                "success": True,
                "links": {"internal": [], "external": []},
                "media": {"images": [], "videos": [], "audios": []},
            }
        ]
    }


@pytest.mark.parametrize("with_internal_key", [False, True])
def test_http_level_integration_with_real_crawl4ai_shape(
    monkeypatch: pytest.MonkeyPatch,
    with_internal_key: bool,
) -> None:
    """End-to-end: route -> _fetch_page_markdown -> httpx -> _extract_markdown
    -> compute_content_fingerprint, with a real-shaped crawl4ai response.

    Parametrised to cover both auth modes (no key = no Authorization header,
    key set = Bearer header sent).
    """
    captured_clients: list[_FakeAsyncClient] = []

    def _client_factory(*_args: Any, **kwargs: Any) -> _FakeAsyncClient:
        client = _FakeAsyncClient(
            response=_FakeResponse(
                status_code=200,
                json_data=_crawl4ai_single_page_response(
                    _PORTAL_TEST_URL, _LONG_MARKDOWN
                ),
            ),
            **kwargs,
        )
        captured_clients.append(client)
        return client

    monkeypatch.setattr(
        "app.routes.fingerprint.httpx.AsyncClient", _client_factory
    )

    # Build the test client AFTER patching httpx so the route uses the fake.
    # Note: we deliberately do NOT call _patch_crawl here — we want
    # _fetch_page_markdown to run for real and exercise the httpx path.
    app = FastAPI()
    app.include_router(fingerprint_router, prefix="/api/v1")
    monkeypatch.setattr(
        "app.routes.fingerprint._require_portal_call", lambda _request: None
    )
    monkeypatch.setattr(
        "app.routes.fingerprint.Settings",
        lambda: SimpleNamespace(
            crawl4ai_api_url="http://crawl4ai.test:11235",
            crawl4ai_internal_key="secret-key" if with_internal_key else "",
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/compute-fingerprint",
        json={"url": _PORTAL_TEST_URL},
    )

    # End-to-end success
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["fingerprint"]) == 16
    assert int(body["fingerprint"], 16) >= 0  # valid hex
    assert body["word_count"] >= 20

    # Exactly one POST to crawl4ai
    assert len(captured_clients) == 1
    fake = captured_clients[0]
    assert len(fake.calls) == 1
    posted_url, payload, headers = fake.calls[0]

    # _build_crawl_payload contract
    assert posted_url == "http://crawl4ai.test:11235/crawl"
    assert payload is not None
    assert payload["urls"] == [_PORTAL_TEST_URL]
    assert payload["crawler_config"]["type"] == "CrawlerRunConfig"
    crawl_params = payload["crawler_config"]["params"]
    assert crawl_params["cache_mode"] == "bypass"
    assert "nav" in crawl_params["excluded_tags"]
    assert crawl_params["markdown_generator"]["type"] == "DefaultMarkdownGenerator"

    # Auth header contract
    if with_internal_key:
        assert headers == {"Authorization": "Bearer secret-key"}
    else:
        assert headers == {}


def test_http_level_integration_string_markdown_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """crawl4ai sometimes emits ``markdown`` as a string (older response
    versions or simplified configs). _extract_markdown's branch must
    cover that shape too — pin it.
    """

    def _client_factory(*_args: Any, **kwargs: Any) -> _FakeAsyncClient:
        return _FakeAsyncClient(
            response=_FakeResponse(
                status_code=200,
                json_data={
                    "results": [
                        {
                            "url": _PORTAL_TEST_URL,
                            "markdown": _LONG_MARKDOWN,  # string, not dict
                            "html": "",
                            "success": True,
                        }
                    ]
                },
            ),
            **kwargs,
        )

    monkeypatch.setattr(
        "app.routes.fingerprint.httpx.AsyncClient", _client_factory
    )

    app = FastAPI()
    app.include_router(fingerprint_router, prefix="/api/v1")
    monkeypatch.setattr(
        "app.routes.fingerprint._require_portal_call", lambda _request: None
    )
    monkeypatch.setattr(
        "app.routes.fingerprint.Settings",
        lambda: SimpleNamespace(
            crawl4ai_api_url="http://crawl4ai.test:11235",
            crawl4ai_internal_key="",
        ),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/compute-fingerprint",
        json={"url": _PORTAL_TEST_URL},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["fingerprint"]) == 16


def test_source_does_not_import_deleted_webcrawler_module() -> None:
    """REQ-31.1: the dormant ``app.adapters.webcrawler`` import was the
    proximate cause of HY-31. Pin an AST-based static check so a future
    refactor can't silently re-introduce the same dead import.

    The check inspects ``import``/``from ... import`` nodes only — the
    HY-31 post-fix docstring deliberately references the dead module
    name in prose, which a naive substring scan would false-positive on.
    """
    import ast
    import inspect

    import app.routes.fingerprint as fingerprint_module

    tree = ast.parse(inspect.getsource(fingerprint_module))

    dead_module = "app.adapters.webcrawler"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != dead_module, (
                    f"fingerprint.py must not import {dead_module} — "
                    "module was deleted by SPEC-CRAWLER-004 Fase F "
                    "(commit 2295bc0c)."
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module != dead_module, (
                f"fingerprint.py must not 'from {dead_module} import ...' — "
                "module was deleted by SPEC-CRAWLER-004 Fase F "
                "(commit 2295bc0c)."
            )
