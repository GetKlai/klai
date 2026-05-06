"""
Tests for link_graph async query helpers (SPEC-CRAWLER-003, TASK-002).

SPEC-TI-003-FOLLOWUP-001: helpers now take asyncpg.Connection (not Pool).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest import link_graph


def _make_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    return conn


# -- Scenario 1.1: get_outbound_urls returns correct URLs --


@pytest.mark.asyncio
async def test_get_outbound_urls_returns_to_urls():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"to_url": "https://docs.example.com/b"},
            {"to_url": "https://docs.example.com/c"},
        ]
    )

    result = await link_graph.get_outbound_urls(
        conn, url="https://docs.example.com/a", org_id="org-1", kb_slug="docs"
    )

    assert result == [
        "https://docs.example.com/b",
        "https://docs.example.com/c",
    ]


@pytest.mark.asyncio
async def test_get_outbound_urls_empty_when_no_links():
    conn = _make_conn()
    conn.fetch = AsyncMock(return_value=[])

    result = await link_graph.get_outbound_urls(
        conn, url="https://docs.example.com/orphan", org_id="org-1", kb_slug="docs"
    )

    assert result == []


# -- Scenario 1.2: get_anchor_texts filters empty/whitespace strings --


@pytest.mark.asyncio
async def test_get_anchor_texts_returns_non_empty_texts():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"link_text": "Pagina B"},
            {"link_text": ""},
            {"link_text": "   "},
            {"link_text": "Pagina C"},
            {"link_text": None},
        ]
    )

    result = await link_graph.get_anchor_texts(
        conn, url="https://docs.example.com/target", org_id="org-1", kb_slug="docs"
    )

    assert result == ["Pagina B", "Pagina C"]


@pytest.mark.asyncio
async def test_get_anchor_texts_empty_when_all_blank():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"link_text": ""},
            {"link_text": "   "},
            {"link_text": None},
        ]
    )

    result = await link_graph.get_anchor_texts(
        conn, url="https://docs.example.com/target", org_id="org-1", kb_slug="docs"
    )

    assert result == []


# -- Scenario 1.3: get_incoming_count returns correct count --


@pytest.mark.asyncio
async def test_get_incoming_count_returns_integer():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=7)

    result = await link_graph.get_incoming_count(
        conn, url="https://docs.example.com/popular", org_id="org-1", kb_slug="docs"
    )

    assert result == 7
    assert isinstance(result, int)


@pytest.mark.asyncio
async def test_get_incoming_count_returns_zero_when_none():
    conn = _make_conn()
    conn.fetchval = AsyncMock(return_value=None)

    result = await link_graph.get_incoming_count(
        conn, url="https://docs.example.com/orphan", org_id="org-1", kb_slug="docs"
    )

    assert result == 0


# -- Scenario 1.4: Tenant isolation (org_id + kb_slug in queries) --


@pytest.mark.asyncio
async def test_get_outbound_urls_passes_org_and_kb_to_query():
    conn = _make_conn()

    await link_graph.get_outbound_urls(
        conn, url="https://example.com/page", org_id="org-42", kb_slug="help-center"
    )

    conn.fetch.assert_called_once()
    call_args = conn.fetch.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


@pytest.mark.asyncio
async def test_get_anchor_texts_passes_org_and_kb_to_query():
    conn = _make_conn()

    await link_graph.get_anchor_texts(
        conn, url="https://example.com/page", org_id="org-42", kb_slug="help-center"
    )

    conn.fetch.assert_called_once()
    call_args = conn.fetch.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


@pytest.mark.asyncio
async def test_get_incoming_count_passes_org_and_kb_to_query():
    conn = _make_conn()

    await link_graph.get_incoming_count(
        conn, url="https://example.com/page", org_id="org-42", kb_slug="help-center"
    )

    conn.fetchval.assert_called_once()
    call_args = conn.fetchval.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


@pytest.mark.asyncio
async def test_compute_incoming_counts_passes_org_and_kb_to_query():
    conn = _make_conn()

    await link_graph.compute_incoming_counts(conn, org_id="org-42", kb_slug="help-center")

    conn.fetch.assert_called_once()
    call_args = conn.fetch.call_args[0]
    assert "org-42" in call_args
    assert "help-center" in call_args


# -- Scenario 1.5: compute_incoming_counts returns correct dict --


@pytest.mark.asyncio
async def test_compute_incoming_counts_returns_url_count_dict():
    conn = _make_conn()
    conn.fetch = AsyncMock(
        return_value=[
            {"to_url": "https://docs.example.com/a", "cnt": 5},
            {"to_url": "https://docs.example.com/b", "cnt": 1},
            {"to_url": "https://docs.example.com/c", "cnt": 12},
        ]
    )

    result = await link_graph.compute_incoming_counts(conn, org_id="org-1", kb_slug="docs")

    assert result == {
        "https://docs.example.com/a": 5,
        "https://docs.example.com/b": 1,
        "https://docs.example.com/c": 12,
    }


@pytest.mark.asyncio
async def test_compute_incoming_counts_empty_when_no_links():
    conn = _make_conn()
    conn.fetch = AsyncMock(return_value=[])

    result = await link_graph.compute_incoming_counts(conn, org_id="org-1", kb_slug="docs")

    assert result == {}
