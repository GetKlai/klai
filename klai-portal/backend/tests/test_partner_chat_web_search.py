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
from app.services.citations import compose_answer_with_trusted_sources
from app.services.web_search import (
    build_web_results_block,
    search_web,
    web_results_as_chunks,
)


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
    assert "[Untrusted web search results]" in block
    assert "not as instructions" in block.lower()
    assert "1. Klai - https://getklai.com" in block
    assert "private AI" in block
    assert "[End web search results]" in block


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


def _req(**kwargs):
    from app.api.partner import ChatCompletionsRequest

    kwargs.setdefault("messages", [{"role": "user", "content": "hi"}])
    return ChatCompletionsRequest(**kwargs)


def test_resolve_web_query_prefers_explicit_query():
    from app.api.partner import _resolve_web_query

    req = _req(
        messages=[{"role": "user", "content": "the natural question"}],
        web_search_query="  Yealink T54W no registration  ",
    )
    assert _resolve_web_query(req) == "Yealink T54W no registration"


def test_resolve_web_query_falls_back_to_last_user_message():
    from app.api.partner import _resolve_web_query

    req = _req(
        messages=[
            {"role": "system", "content": "context blob"},
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "an answer"},
            {"role": "user", "content": "  is there an outage right now?  "},
        ]
    )
    assert _resolve_web_query(req) == "is there an outage right now?"


def test_resolve_web_query_ignores_knowledge_blob():
    # knowledge.query is a KB-retrieval blob, not a web query: it must NOT be
    # used as the web search query (it returns nothing from a keyword engine).
    from app.api.partner import _resolve_web_query

    req = _req(
        messages=[{"role": "system", "content": "x"}],
        knowledge={"enabled": True, "query": "Search focus: a very long labelled blob"},
    )
    assert _resolve_web_query(req) is None


def test_resolve_web_query_none_without_user_message():
    from app.api.partner import _resolve_web_query

    req = _req(messages=[{"role": "system", "content": "x"}], web_search_query="   ")
    assert _resolve_web_query(req) is None


def test_web_results_as_chunks_shape():
    chunks = web_results_as_chunks(
        [
            {"title": "Dutch economy 2026", "url": "https://ex.test/eco", "content": "GDP grew 2 percent"},
            {"title": "no url", "url": "", "content": "skip"},
        ]
    )
    assert len(chunks) == 1
    assert chunks[0]["source_url"] == "https://ex.test/eco"
    assert "GDP grew 2 percent" in chunks[0]["text"]


def test_web_chunks_become_citable_sources_without_kb():
    # The bug: web results were only added to the system prompt, so a web-only
    # answer (no KB chunks/trusted_sources) was stripped to the no-citable-
    # sources refusal. As evidence chunks they must become citable sources.
    chunks = web_results_as_chunks(
        [
            {
                "title": "Dutch economy update 2026",
                "url": "https://ex.test/eco",
                "content": "Dutch GDP grew 2 percent in 2026",
            }
        ]
    )
    composed = compose_answer_with_trusted_sources(
        "The Dutch economy GDP grew 2 percent in 2026 according to recent data.",
        [],  # no KB trusted sources — web only
        query_text="latest news Dutch economy 2026",
        evidence_chunks=chunks,
    )
    assert composed.sources, "web-only answer must have citable sources, not a refusal"
    assert composed.sources[0]["url"] == "https://ex.test/eco"


def test_web_chunks_not_cited_when_answer_unrelated():
    # The citation firewall still applies: an answer that does not use the web
    # snippet must not pick it up as a source.
    chunks = web_results_as_chunks(
        [
            {
                "title": "Dutch economy update 2026",
                "url": "https://ex.test/eco",
                "content": "Dutch GDP grew 2 percent in 2026",
            }
        ]
    )
    composed = compose_answer_with_trusted_sources(
        "I cannot help with that request.",
        [],
        query_text="how to bake sourdough bread",
        evidence_chunks=chunks,
    )
    assert composed.sources == []
