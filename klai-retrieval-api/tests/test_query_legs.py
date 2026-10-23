"""The translated and the alias-expanded search legs (plan step 6)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from retrieval_api.models import RetrieveRequest
from retrieval_api.services import coreference, search

EN_QUERY = "How do I install the desktop application on my laptop at home?"
DE_QUERY = "Wie installiere ich die Desktop-Anwendung auf meinem Laptop zu Hause?"
DUTCH_CHUNKS = [
    "Je installeert de applicatie via de downloadpagina en logt daarna in met je account.",
    "Ga naar de instellingen van je toestel en kies daar voor een nieuwe verbinding.",
    "Als het installeren niet lukt, controleer dan of je de laatste versie hebt gedownload.",
]
ENGLISH_CHUNKS = [
    "You install the application from the download page and then sign in with your account.",
    "Go to the settings of your device and choose a new connection there.",
    "If the installation fails, check that you downloaded the latest version of the app.",
]
# Two thirds Dutch, one third English: both count as a language of the KB.
BILINGUAL_CHUNKS = DUTCH_CHUNKS * 2 + ENGLISH_CHUNKS


@pytest.fixture(autouse=True)
def _clear_caches():
    search._client = None
    search._kb_language_cache.clear()
    search._alias_cache.clear()
    yield
    search._client = None
    search._kb_language_cache.clear()
    search._alias_cache.clear()


def _client(chunk_texts: list[str]) -> AsyncMock:
    client = AsyncMock()
    client.scroll.return_value = (
        [SimpleNamespace(payload={"text": text}) for text in chunk_texts],
        None,
    )
    client.query_points.return_value = SimpleNamespace(points=[])
    return client


def _request(**overrides) -> RetrieveRequest:
    return RetrieveRequest(**{"query": "q", "org_id": "org-1", "kb_slugs": ["help"], **overrides})


async def _search(client: AsyncMock, *, translation: str | None, aliases=None, query=EN_QUERY):
    translate = AsyncMock(return_value=translation)
    embed = AsyncMock(return_value=[0.3, 0.4])
    with (
        patch.object(search, "_get_client", return_value=client),
        patch.object(coreference, "translate", translate),
        patch.object(search, "_alias_groups", AsyncMock(return_value=aliases or [])),
        patch.object(search, "embed_single", embed),
        patch.object(search, "embed_sparse", AsyncMock(return_value=None)),
    ):
        req = _request(query=query)
        legs = await search.extra_query_legs(query, req)
        await search.hybrid_search([0.1, 0.2], req, 10, extra_legs=legs)
    prefetch = client.query_points.call_args.kwargs["prefetch"]
    return translate, embed, prefetch


@pytest.mark.asyncio
async def test_query_outside_the_kb_languages_gets_one_translation_into_the_main_one():
    translate, embed, prefetch = await _search(
        _client(BILINGUAL_CHUNKS),
        translation="Hoe installeer ik de desktopapplicatie?",
        query=DE_QUERY,
    )

    translate.assert_awaited_once_with(DE_QUERY, "nl")
    embed.assert_awaited_once_with("Hoe installeer ik de desktopapplicatie?")
    assert len(prefetch) == 3


@pytest.mark.asyncio
async def test_query_in_one_of_the_kb_languages_gets_no_translation():
    translate, embed, prefetch = await _search(_client(BILINGUAL_CHUNKS), translation="unused")

    translate.assert_not_awaited()
    embed.assert_not_awaited()
    assert len(prefetch) == 2


@pytest.mark.asyncio
async def test_failed_translation_leaves_the_search_unchanged():
    _, embed, prefetch = await _search(_client(DUTCH_CHUNKS), translation=None)

    embed.assert_not_awaited()
    assert len(prefetch) == 2


@pytest.mark.asyncio
async def test_an_alias_in_the_query_adds_the_expanded_leg():
    query = "Why does the windows app keep asking me to sign in again?"
    _, embed, prefetch = await _search(
        _client(ENGLISH_CHUNKS),
        translation=None,
        aliases=[["Web client", "Windows app", "desktop app"], ["Fax", "e-fax"]],
        query=query,
    )

    embed.assert_awaited_once_with(f"{query} Web client desktop app")
    assert len(prefetch) == 3


def test_an_alias_matches_whole_words_only():
    assert search.expand_with_aliases("the faxes arrived", [["Fax", "e-fax"]]) is None


@pytest.mark.asyncio
async def test_the_main_search_runs_when_the_extra_step_times_out():
    async def hang(*_args):
        await asyncio.sleep(10)

    client = _client(DUTCH_CHUNKS)
    with (
        patch.object(search, "_get_client", return_value=client),
        patch.object(search, "_extra_query_texts", hang),
        patch.object(search, "_EXTRA_LEGS_TIMEOUT", 0.05),
    ):
        started = time.monotonic()
        legs = await search.extra_query_legs(EN_QUERY, _request())
        await search.hybrid_search([0.1, 0.2], _request(), 10, extra_legs=legs)

    assert legs == []
    assert time.monotonic() - started < 1
    assert len(client.query_points.call_args.kwargs["prefetch"]) == 2


def _sample_filter(client: AsyncMock) -> Filter:
    return client.scroll.call_args.kwargs["scroll_filter"]


@pytest.mark.asyncio
async def test_the_language_sample_is_scoped_to_the_org_and_the_selected_kbs():
    client = _client(DUTCH_CHUNKS)
    with patch.object(search, "_get_client", return_value=client):
        await search._kb_languages(_request(kb_slugs=["help", "sales"]))

    must = _sample_filter(client).must
    assert FieldCondition(key="org_id", match=MatchValue(value="org-1")) in must
    assert FieldCondition(key="kb_slug", match=MatchAny(any=["help", "sales"])) in must


@pytest.mark.asyncio
async def test_without_a_kb_selection_the_language_sample_leaves_out_other_users_private_kbs():
    client = _client(DUTCH_CHUNKS)
    with patch.object(search, "_get_client", return_value=client):
        await search._kb_languages(_request(kb_slugs=None, user_id="user-1"))

    sample = _sample_filter(client)
    assert sample.must == search._scope_filter(_request(kb_slugs=None, user_id="user-1"))


@pytest.mark.asyncio
async def test_a_failed_language_sample_is_not_retried_on_every_request():
    client = _client(DUTCH_CHUNKS)
    client.scroll.side_effect = TimeoutError
    with patch.object(search, "_get_client", return_value=client):
        assert await search._kb_languages(_request()) == []
        assert await search._kb_languages(_request()) == []

    assert client.scroll.await_count == 1


@pytest.mark.asyncio
async def test_alias_groups_leave_out_other_users_personal_kbs_and_decode_jsonb_text():
    pool = AsyncMock()
    pool.fetch.return_value = [{"aliases": '[["Web client", "Windows app"]]'}, {"aliases": "[]"}]
    with patch.object(search, "get_pool", return_value=pool):
        groups = await search._alias_groups(_request(kb_slugs=None, user_id="user-1"))

    assert groups == [["Web client", "Windows app"]]
    sql, org_id, slugs, user_id = pool.fetch.call_args.args
    assert (org_id, slugs, user_id) == ("org-1", [], "user-1")
    assert "kb.owner_type = 'org' OR kb.owner_user_id = $3" in sql


@pytest.mark.asyncio
async def test_translate_returns_none_when_the_model_times_out():
    with patch.object(coreference, "_call_llm", AsyncMock(side_effect=TimeoutError)):
        assert await coreference.translate(EN_QUERY, "nl") is None
