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
