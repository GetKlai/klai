"""Web search for the Partner API (server-side SearXNG fetch + cite).

`web_search: true` makes portal-api query the same SearXNG instance the chat
surfaces use, inject the results as an untrusted context block into the system
prompt, AND feed them to the citation composer as a separate web tier so they
become citable sources tagged `origin: "web"`. Gated per API key
(`web_search` permission) and never run for public widget keys.

These tests cover:
- search_web: calls SearXNG /search?format=json, parses + bounds results,
  fail-open (returns [] on any error).
- build_web_results_block: untrusted framing; web_results_as_chunks shape.
- the permission gate and web-query resolution the handler relies on.
- _compose_backend_managed_answer: KB/web kept as separate origin-tagged
  tiers, web-only is citable (not refused), both-empty is refused.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

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


def test_web_search_query_rejects_oversized_explicit_query():
    long_query = "x" * 513
    with pytest.raises(ValidationError):
        _req(web_search_query=long_query)


def test_resolve_web_query_bounds_derived_user_message():
    from app.api.partner import _MAX_WEB_SEARCH_QUERY_CHARS, _resolve_web_query

    req = _req(messages=[{"role": "user", "content": "x" * (_MAX_WEB_SEARCH_QUERY_CHARS + 100)}])
    query = _resolve_web_query(req)
    assert query == "x" * _MAX_WEB_SEARCH_QUERY_CHARS


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


def test_resolve_web_query_handles_multimodal_content():
    # OpenAI-style content arrays must not silently drop the user's question.
    from app.api.partner import _resolve_web_query

    req = _req(
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Is there an outage"},
                    {"type": "image_url", "image_url": {"url": "https://x.test/a.png"}},
                    {"type": "text", "text": "right now?"},
                ],
            }
        ]
    )
    assert _resolve_web_query(req) == "Is there an outage right now?"


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


def test_compose_keeps_kb_and_web_as_separate_tiers():
    # KB and web are distinct trust tiers: each source is tagged with its origin
    # and the merged list gets one contiguous label sequence.
    from app.services.partner_chat import _compose_backend_managed_answer

    answer = "Disable SIP-ALG on the router for the Yealink phone. Dutch GDP grew 2 percent in 2026."
    kb_chunks = [
        {
            "source_url": "https://kb.test/sip",
            "title": "SIP-ALG guide",
            "text": "Disable SIP-ALG on the router for the Yealink phone",
        }
    ]
    web_chunks = web_results_as_chunks(
        [{"title": "Economy 2026", "url": "https://web.test/eco", "content": "Dutch GDP grew 2 percent in 2026"}]
    )
    _content, sources, _decision = _compose_backend_managed_answer(
        answer, [], kb_chunks, "yealink sip-alg dutch economy 2026", web_chunks
    )
    origins = {s["url"]: s["origin"] for s in sources}
    assert origins.get("https://kb.test/sip") == "kb"
    assert origins.get("https://web.test/eco") == "web"
    assert [s["label"] for s in sources] == [str(i) for i in range(1, len(sources) + 1)]


def test_compose_web_only_not_refused():
    from app.services.partner_chat import _compose_backend_managed_answer

    answer = "Dutch GDP grew 2 percent in 2026 according to recent data."
    web_chunks = web_results_as_chunks(
        [{"title": "Economy 2026", "url": "https://web.test/eco", "content": "Dutch GDP grew 2 percent in 2026"}]
    )
    content, sources, _decision = _compose_backend_managed_answer(answer, [], [], "dutch economy 2026", web_chunks)
    assert sources and all(s["origin"] == "web" for s in sources)
    assert "2 percent" in content


def test_compose_refuses_when_no_kb_and_no_web():
    from app.services.partner_chat import _compose_backend_managed_answer

    _content, sources, _decision = _compose_backend_managed_answer(
        "Some ungrounded statement.", [], [], "unrelated query", None
    )
    assert sources == []


def test_compose_validates_web_against_web_query_not_kb_blob():
    # HIGH regression: web sources must be validated against the concise web
    # query, NOT the long KB-retrieval blob (user_query). A tangential web result
    # that shares blob tokens otherwise dominates query_support_tokens and the
    # relevant web source gets rejected as query_not_supported -> refusal.
    from app.services.partner_chat import _compose_backend_managed_answer

    answer = "There is an outage at provider X affecting calls right now."
    web_chunks = web_results_as_chunks(
        [
            {
                "title": "Provider X outage",
                "url": "https://status.test/x",
                "content": "Provider X reports an outage affecting calls right now",
            },
            {
                "title": "Yealink router setup",
                "url": "https://kb.test/yealink",
                "content": "How to configure your Yealink router registratie ticket",
            },
        ]
    )
    blob = "Yealink router registratie ticket model"

    # With the KB blob as the web query_text, the relevant web source is rejected.
    _c, blob_sources, _d = _compose_backend_managed_answer(answer, [], [], blob, web_chunks, web_query=None)
    assert blob_sources == []

    # With the concise web query threaded through, it is cited.
    _c2, web_sources, _d2 = _compose_backend_managed_answer(
        answer, [], [], blob, web_chunks, web_query="outage provider X affecting calls"
    )
    assert [s["url"] for s in web_sources] == ["https://status.test/x"]
    assert web_sources[0]["origin"] == "web"


def test_compose_dedupes_same_url_across_kb_and_web():
    # If the KB and the web both surface the same URL, emit it once (KB wins).
    from app.services.partner_chat import _compose_backend_managed_answer

    answer = "Disable SIP-ALG on the router for the Yealink phone."
    same = "https://help.test/sip-alg"
    kb_chunks = [
        {"source_url": same, "title": "SIP-ALG guide", "text": "Disable SIP-ALG on the router for the Yealink phone"}
    ]
    web_chunks = web_results_as_chunks(
        [{"title": "SIP-ALG guide", "url": same, "content": "Disable SIP-ALG on the router for the Yealink phone"}]
    )
    _content, sources, _decision = _compose_backend_managed_answer(
        answer, [], kb_chunks, "yealink sip-alg router", web_chunks, web_query="yealink sip-alg router"
    )
    urls = [s["url"] for s in sources]
    assert urls.count(same) == 1
    assert next(s for s in sources if s["url"] == same)["origin"] == "kb"


@pytest.mark.asyncio
async def test_maybe_apply_web_search_skips_widget(monkeypatch):
    import app.api.partner as partner

    search = AsyncMock(return_value=[{"title": "T", "url": "https://x.test/1", "content": "c"}])
    monkeypatch.setattr(partner, "search_web", search)
    req = _req(web_search=True, messages=[{"role": "user", "content": "is there an outage?"}])
    prompt, chunks, web_query = await partner._maybe_apply_web_search(
        request=req, auth=_auth({"chat": True, "web_search": True}), is_widget_chat=True, system_prompt="sp"
    )
    assert (prompt, chunks, web_query) == ("sp", [], None)
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_apply_web_search_requires_permission(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "search_web", AsyncMock(return_value=[]))
    req = _req(web_search=True, messages=[{"role": "user", "content": "q"}])
    with pytest.raises(HTTPException) as exc:
        await partner._maybe_apply_web_search(
            request=req, auth=_auth({"chat": True}), is_widget_chat=False, system_prompt="sp"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_maybe_apply_web_search_searches_with_resolved_query(monkeypatch):
    import app.api.partner as partner

    search = AsyncMock(return_value=[{"title": "T", "url": "https://x.test/1", "content": "c"}])
    monkeypatch.setattr(partner, "search_web", search)
    req = _req(web_search=True, web_search_query="my concise query", messages=[{"role": "user", "content": "q"}])
    prompt, chunks, web_query = await partner._maybe_apply_web_search(
        request=req, auth=_auth({"chat": True, "web_search": True}), is_widget_chat=False, system_prompt="sp"
    )
    assert web_query == "my concise query"
    assert chunks and chunks[0]["source_url"] == "https://x.test/1"
    assert search.await_args.args[0] == "my concise query"
    assert "Untrusted web search results" in prompt


@pytest.mark.asyncio
async def test_chat_handler_web_search_requires_partner_permission(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=[]))
    monkeypatch.setattr(partner, "retrieve_context", AsyncMock(return_value=([], "sp", [])))
    search = AsyncMock(return_value=[])
    monkeypatch.setattr(partner, "search_web", search)
    req = _req(web_search=True, stream=False, messages=[{"role": "user", "content": "q"}])

    with pytest.raises(HTTPException) as exc:
        await partner.chat_completions(
            request=req,
            http_request=MagicMock(headers={}, client=MagicMock(host="127.0.0.1")),
            auth=_auth({"chat": True}),
            db=AsyncMock(),
        )

    assert exc.value.status_code == 403
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_handler_web_search_never_runs_for_widget_key(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=[]))
    monkeypatch.setattr(partner, "_widget_system_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(partner, "_widget_page_context_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(partner, "retrieve_context", AsyncMock(return_value=([], "sp", [])))
    search = AsyncMock(return_value=[{"title": "T", "url": "https://x.test/1", "content": "c"}])
    chat = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(partner, "search_web", search)
    monkeypatch.setattr(partner, "chat_completion_non_streaming", chat)
    req = _req(web_search=True, stream=False, messages=[{"role": "user", "content": "q"}])
    auth = _auth({"chat": True, "web_search": True})
    auth.key_id = "wgt_test_widget"
    db = AsyncMock()
    widget_uuid_result = MagicMock()
    widget_uuid_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=widget_uuid_result)

    await partner.chat_completions(
        request=req,
        http_request=MagicMock(headers={}, client=MagicMock(host="127.0.0.1")),
        auth=auth,
        db=db,
    )

    search.assert_not_awaited()
    chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_handler_web_search_allowed_for_partner_key(monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=[]))
    monkeypatch.setattr(partner, "retrieve_context", AsyncMock(return_value=([], "sp", [])))
    search = AsyncMock(return_value=[{"title": "T", "url": "https://x.test/1", "content": "c"}])
    chat = AsyncMock(return_value={"choices": [{"message": {"content": "ok"}}]})
    monkeypatch.setattr(partner, "search_web", search)
    monkeypatch.setattr(partner, "chat_completion_non_streaming", chat)
    req = _req(web_search=True, stream=False, web_search_query="concise", messages=[{"role": "user", "content": "q"}])

    await partner.chat_completions(
        request=req,
        http_request=MagicMock(headers={}, client=MagicMock(host="127.0.0.1")),
        auth=_auth({"chat": True, "web_search": True}),
        db=AsyncMock(),
    )

    search.assert_awaited_once()
    assert search.await_args.args[0] == "concise"
    assert chat.await_args.kwargs["web_query"] == "concise"
    assert chat.await_args.kwargs["web_chunks"][0]["source_url"] == "https://x.test/1"


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
