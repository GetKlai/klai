"""
Tests for link_graph async query helpers (SPEC-CRAWLER-003, TASK-002).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from knowledge_ingest import link_graph


def _make_pool():
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


# -- Scenario 1.1: get_outbound_urls returns correct URLs --


@pytest.mark.asyncio
async def test_get_outbound_urls_returns_to_urls():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[
        {"to_url": "https://docs.example.com/b"},
        {"to_url": "https://docs.example.com/c"},
    ])

    result = await link_graph.get_outbound_urls(
        url="https://docs.example.com/a",
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == [
        "https://docs.example.com/b",
        "https://docs.example.com/c",
    ]


@pytest.mark.asyncio
async def test_get_outbound_urls_empty_when_no_links():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])

    result = await link_graph.get_outbound_urls(
        url="https://docs.example.com/orphan",
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == []


# -- Scenario 1.2: get_anchor_texts filters empty/whitespace strings --


@pytest.mark.asyncio
async def test_get_anchor_texts_returns_non_empty_texts():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[
        {"link_text": "Pagina B"},
        {"link_text": ""},
        {"link_text": "   "},
        {"link_text": "Pagina C"},
        {"link_text": None},
    ])

    result = await link_graph.get_anchor_texts(
        url="https://docs.example.com/target",
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == ["Pagina B", "Pagina C"]


@pytest.mark.asyncio
async def test_get_anchor_texts_empty_when_all_blank():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[
        {"link_text": ""},
        {"link_text": "   "},
        {"link_text": None},
    ])

    result = await link_graph.get_anchor_texts(
        url="https://docs.example.com/target",
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == []


# -- Scenario 1.3: get_incoming_count returns correct count --


@pytest.mark.asyncio
async def test_get_incoming_count_returns_integer():
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=7)

    result = await link_graph.get_incoming_count(
        url="https://docs.example.com/popular",
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == 7
    assert isinstance(result, int)


@pytest.mark.asyncio
async def test_get_incoming_count_returns_zero_when_none():
    pool = _make_pool()
    pool.fetchval = AsyncMock(return_value=None)

    result = await link_graph.get_incoming_count(
        url="https://docs.example.com/orphan",
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == 0


# -- Scenario 1.4: Tenant isolation (org_id + kb_slug in queries) --


@pytest.mark.asyncio
async def test_get_outbound_urls_passes_org_and_kb_to_query():
    pool = _make_pool()

    await link_graph.get_outbound_urls(
        url="https://example.com/page",
        org_id="org-42",
        kb_slug="help-center",
        pool=pool,
    )

    pool.fetch.assert_called_once()
    call_args = pool.fetch.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


@pytest.mark.asyncio
async def test_get_anchor_texts_passes_org_and_kb_to_query():
    pool = _make_pool()

    await link_graph.get_anchor_texts(
        url="https://example.com/page",
        org_id="org-42",
        kb_slug="help-center",
        pool=pool,
    )

    pool.fetch.assert_called_once()
    call_args = pool.fetch.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


@pytest.mark.asyncio
async def test_get_incoming_count_passes_org_and_kb_to_query():
    pool = _make_pool()

    await link_graph.get_incoming_count(
        url="https://example.com/page",
        org_id="org-42",
        kb_slug="help-center",
        pool=pool,
    )

    pool.fetchval.assert_called_once()
    call_args = pool.fetchval.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


@pytest.mark.asyncio
async def test_compute_incoming_counts_passes_org_and_kb_to_query():
    pool = _make_pool()

    await link_graph.compute_incoming_counts(
        org_id="org-42",
        kb_slug="help-center",
        pool=pool,
    )

    pool.fetch.assert_called_once()
    call_args = pool.fetch.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


# -- Scenario 1.5: compute_incoming_counts returns correct dict --


@pytest.mark.asyncio
async def test_compute_incoming_counts_returns_url_count_dict():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[
        {"to_url": "https://docs.example.com/a", "cnt": 5},
        {"to_url": "https://docs.example.com/b", "cnt": 1},
        {"to_url": "https://docs.example.com/c", "cnt": 12},
    ])

    result = await link_graph.compute_incoming_counts(
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == {
        "https://docs.example.com/a": 5,
        "https://docs.example.com/b": 1,
        "https://docs.example.com/c": 12,
    }


@pytest.mark.asyncio
async def test_compute_incoming_counts_empty_when_no_links():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])

    result = await link_graph.compute_incoming_counts(
        org_id="org-1",
        kb_slug="docs",
        pool=pool,
    )

    assert result == {}
