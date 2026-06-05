"""Web search for the Partner API (server-side SearXNG fetch + inject).

`web_search: true` makes portal-api query the same SearXNG instance the chat
surfaces use, then inject the results into the system prompt. This is gated per
API key (`web_search` permission) and never runs for public widget keys.

These tests cover the building blocks:
- search_web: calls SearXNG /search?format=json, parses + bounds results,
  and is fail-open (returns [] on any error).
- build_web_results_block: renders results, empty for no results.
- the permission gate that the handler relies on.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.partner_dependencies import PartnerAuthContext, require_permission
from app.services.web_search import build_web_results_block, search_web


def _fake_settings() -> MagicMock:
    s = MagicMock()
    s.searxng_url = "http://searxng:8080"
    return s


def _stub_get(json_body=None, raises: Exception | None = None) -> tuple[MagicMock, dict]:
    captured: dict = {}

    async def fake_get(url, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        if raises is not None:
            raise raises
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=json_body or {})
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=fake_get)

    @asynccontextmanager
    async def fake_client(*_args, **_kwargs):
        yield client

    return fake_client, captured


@pytest.mark.asyncio
async def test_search_web_calls_searxng_json_and_parses():
    body = {
        "results": [
            {"title": "Result one", "url": "https://a.test/1", "content": "first snippet"},
            {"title": "Result two", "url": "https://b.test/2", "content": "second snippet"},
        ]
    }
    fake_client, captured = _stub_get(json_body=body)
    with patch("app.services.web_search.httpx.AsyncClient", fake_client):
        results = await search_web("latest news", settings=_fake_settings(), limit=5)

    assert captured["url"] == "http://searxng:8080/search"
    assert captured["params"] == {"q": "latest news", "format": "json"}
    assert results == [
        {"title": "Result one", "url": "https://a.test/1", "content": "first snippet"},
        {"title": "Result two", "url": "https://b.test/2", "content": "second snippet"},
    ]


@pytest.mark.asyncio
async def test_search_web_respects_limit_and_skips_invalid():
    body = {
        "results": [
            {"title": "ok", "url": "https://a.test/1", "content": "x"},
            {"title": "no url", "url": "", "content": "y"},
            {"title": "ok2", "url": "https://b.test/2", "content": "z"},
            {"title": "ok3", "url": "https://c.test/3", "content": "w"},
        ]
    }
    fake_client, _ = _stub_get(json_body=body)
    with patch("app.services.web_search.httpx.AsyncClient", fake_client):
        results = await search_web("q", settings=_fake_settings(), limit=2)
    assert [r["url"] for r in results] == ["https://a.test/1", "https://b.test/2"]


@pytest.mark.asyncio
async def test_search_web_fail_open_on_error():
    fake_client, _ = _stub_get(raises=RuntimeError("searxng down"))
    with patch("app.services.web_search.httpx.AsyncClient", fake_client):
        results = await search_web("q", settings=_fake_settings())
    assert results == []


@pytest.mark.asyncio
async def test_search_web_empty_query_short_circuits():
    fake_client, captured = _stub_get(json_body={"results": []})
    with patch("app.services.web_search.httpx.AsyncClient", fake_client):
        results = await search_web("   ", settings=_fake_settings())
    assert results == []
    assert captured == {}  # never called SearXNG


def test_build_web_results_block_empty():
    assert build_web_results_block([]) == ""


def test_build_web_results_block_renders_title_url_content():
    block = build_web_results_block([{"title": "Klai", "url": "https://getklai.com", "content": "private AI"}])
    assert "[Web results]" in block
    assert "1. Klai - https://getklai.com" in block
    assert "private AI" in block
    assert "[End web results]" in block


def _auth(permissions: dict) -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id="key-1",
        org_id=1,
        zitadel_org_id="org-1",
        permissions=permissions,
        kb_access={1: "read"},
        rate_limit_rpm=60,
    )


def test_web_search_permission_gate():
    # Key without the permission is rejected.
    with pytest.raises(HTTPException) as exc:
        require_permission(_auth({"chat": True}), "web_search")
    assert exc.value.status_code == 403
    # Key with the permission passes.
    require_permission(_auth({"chat": True, "web_search": True}), "web_search")
