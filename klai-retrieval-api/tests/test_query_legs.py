"""The translated and the alias-expanded search legs (plan step 6)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.models import RetrieveRequest
from retrieval_api.services import coreference, search

EN_QUERY = "How do I install the desktop application on my laptop at home?"
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
        req = RetrieveRequest(query=query, org_id="org-1", kb_slugs=["help"])
        await search.hybrid_search([0.1, 0.2], req, 10, query_text=query)
    prefetch = client.query_points.call_args.kwargs["prefetch"]
    return translate, embed, prefetch


@pytest.mark.asyncio
async def test_query_in_another_language_than_the_kb_searches_its_translation_too():
    translate, embed, prefetch = await _search(
        _client(DUTCH_CHUNKS), translation="Hoe installeer ik de desktopapplicatie?"
    )

    translate.assert_awaited_once_with(EN_QUERY, "nl")
    embed.assert_awaited_once_with("Hoe installeer ik de desktopapplicatie?")
    assert len(prefetch) == 3


@pytest.mark.asyncio
async def test_query_in_the_kb_language_gets_no_translation():
    translate, embed, prefetch = await _search(_client(ENGLISH_CHUNKS), translation="unused")

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
async def test_alias_groups_are_read_from_the_jsonb_text_asyncpg_returns():
    pool = AsyncMock()
    pool.fetch.return_value = [{"aliases": '[["Web client", "Windows app"]]'}, {"aliases": "[]"}]
    with patch.object(search, "get_pool", return_value=pool):
        req = RetrieveRequest(query="q", org_id="org-1", kb_slugs=["help"])
        assert await search._alias_groups(req, ("help",)) == [["Web client", "Windows app"]]


@pytest.mark.asyncio
async def test_translate_returns_none_when_the_model_times_out():
    with patch.object(coreference, "_call_llm", AsyncMock(side_effect=TimeoutError)):
        assert await coreference.translate(EN_QUERY, "nl") is None
