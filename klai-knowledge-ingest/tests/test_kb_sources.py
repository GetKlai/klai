"""Unit tests for SPEC-PORTAL-KENNIS-001 KB sources queries.

Pin the behaviour of the four ``pg_store`` helpers that aggregate active
artifacts and parent_chunks per KB:

  - count_chunks_per_kb        — bulk chunk count per slug
  - list_kb_sources            — connector aggregates + direct uploads
  - list_artifacts_for_connector — drill-down items under one connector
  - list_chunks_for_artifact   — chunks for one upload artifact

The tests use ``MagicMock`` for the ``asyncpg.Connection`` and assert on the
SQL fragments + bound parameters. They do not exercise a live database — that
is covered by the integration tests of the existing personal/items pathway,
which uses the same query primitives.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest import pg_store

# -- count_chunks_per_kb ----------------------------------------------------


@pytest.mark.asyncio
async def test_count_chunks_per_kb_empty_input_short_circuits() -> None:
    """Empty kb_slugs list → empty dict, no DB call."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    result = await pg_store.count_chunks_per_kb(conn, "org-1", [])

    assert result == {}
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_count_chunks_per_kb_returns_dict_keyed_by_slug() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"kb_slug": "kb-a", "chunk_count": 12},
            {"kb_slug": "kb-b", "chunk_count": 0},
        ]
    )

    result = await pg_store.count_chunks_per_kb(conn, "org-1", ["kb-a", "kb-b"])

    assert result == {"kb-a": 12, "kb-b": 0}
    conn.fetch.assert_awaited_once()
    sql = conn.fetch.await_args.args[0]
    assert "knowledge.parent_chunks" in sql
    assert "knowledge.artifacts" in sql
    assert "GROUP BY a.kb_slug" in sql
    # Tenant + active filters
    assert "org_id = $1" in sql
    assert "kb_slug = ANY($2::text[])" in sql
    assert "belief_time_end = $3" in sql


# -- count_sources_per_kb ---------------------------------------------------


@pytest.mark.asyncio
async def test_count_sources_per_kb_empty_input_short_circuits() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    result = await pg_store.count_sources_per_kb(conn, "org-1", [])

    assert result == {}
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_count_sources_per_kb_returns_dict_keyed_by_slug() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"kb_slug": "kb-a", "bronnen_count": 4},
            {"kb_slug": "kb-b", "bronnen_count": 0},
        ]
    )

    result = await pg_store.count_sources_per_kb(conn, "org-1", ["kb-a", "kb-b"])

    assert result == {"kb-a": 4, "kb-b": 0}
    conn.fetch.assert_awaited_once()
    sql = conn.fetch.await_args.args[0]
    # COALESCE collapses connector-grouped artifacts to one bron-key per
    # connector_id, while uploads (NULL connector_id) fall back to a.id.
    assert "COALESCE" in sql
    assert "source_connector_id" in sql
    assert "knowledge.artifacts" in sql
    assert "GROUP BY a.kb_slug" in sql
    assert "org_id = $1" in sql
    assert "kb_slug = ANY($2::text[])" in sql
    assert "belief_time_end = $3" in sql


# -- list_kb_sources --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_kb_sources_groups_connectors_and_uploads() -> None:
    """Two SQL queries: one grouped on connector_id, one filtered to NULL."""
    conn = MagicMock()
    conn.fetch = AsyncMock(
        side_effect=[
            # Query 1 — connectors
            [
                {"connector_id": "conn-x", "items_count": 5, "chunks_count": 23},
            ],
            # Query 2 — uploads
            [
                {
                    "id": "art-1",
                    "path": "report.pdf",
                    "content_type": "pdf",
                    "created_at": 1700000000,
                    "chunks_count": 7,
                },
            ],
        ]
    )

    result = await pg_store.list_kb_sources(conn, "org-1", "kb-a")

    assert result["connectors"] == [
        {"connector_id": "conn-x", "items_count": 5, "chunks_count": 23},
    ]
    assert result["uploads"] == [
        {
            "id": "art-1",
            "path": "report.pdf",
            "content_type": "pdf",
            "created_at": 1700000000,
            "chunks_count": 7,
        },
    ]
    # Two distinct queries: connectors then uploads
    assert conn.fetch.await_count == 2
    connector_sql = conn.fetch.await_args_list[0].args[0]
    upload_sql = conn.fetch.await_args_list[1].args[0]
    assert "source_connector_id" in connector_sql
    assert "IS NOT NULL" in connector_sql
    assert "extra IS NULL" in upload_sql or "IS NULL" in upload_sql
    assert "ORDER BY a.created_at DESC" in upload_sql


@pytest.mark.asyncio
async def test_list_kb_sources_empty_kb_returns_empty_lists() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=[[], []])

    result = await pg_store.list_kb_sources(conn, "org-1", "empty-kb")

    assert result == {"connectors": [], "uploads": []}


# -- list_artifacts_for_connector ------------------------------------------


@pytest.mark.asyncio
async def test_list_artifacts_for_connector_returns_items_and_total() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "art-1",
                "path": "page-1",
                "content_type": "html",
                "created_at": 1700000000,
                "chunks_count": 3,
            }
        ]
    )
    conn.fetchval = AsyncMock(return_value=42)

    items, total = await pg_store.list_artifacts_for_connector(
        conn, "org-1", "kb-a", "conn-x", limit=10, offset=0
    )

    assert len(items) == 1
    assert items[0]["chunks_count"] == 3
    assert total == 42
    fetch_sql = conn.fetch.await_args.args[0]
    assert "source_connector_id" in fetch_sql
    assert "LIMIT $5 OFFSET $6" in fetch_sql


@pytest.mark.asyncio
async def test_list_artifacts_for_connector_handles_no_total() -> None:
    """fetchval returning None (rare but possible) is normalized to 0."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)

    items, total = await pg_store.list_artifacts_for_connector(
        conn, "org-1", "kb-a", "conn-x", limit=10, offset=0
    )

    assert items == []
    assert total == 0


# -- list_chunks_for_artifact ----------------------------------------------


@pytest.mark.asyncio
async def test_list_chunks_for_artifact_orders_by_position() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"id": 1, "position": 0, "text": "First chunk", "token_count": 50},
            {"id": 2, "position": 1, "text": "Second chunk", "token_count": 60},
        ]
    )
    conn.fetchval = AsyncMock(return_value=2)

    chunks, total = await pg_store.list_chunks_for_artifact(
        conn, "org-1", "art-1", limit=10, offset=0
    )

    assert len(chunks) == 2
    assert chunks[0]["position"] == 0
    assert chunks[0]["text"] == "First chunk"
    assert total == 2
    fetch_sql = conn.fetch.await_args.args[0]
    assert 'ORDER BY "position" ASC' in fetch_sql
    # Tenant guard via org_id present in BOTH queries
    assert "org_id = $2" in fetch_sql
    count_sql = conn.fetchval.await_args.args[0]
    assert "org_id = $2" in count_sql


@pytest.mark.asyncio
async def test_list_chunks_for_artifact_passes_tenant_id_to_count() -> None:
    """org_id MUST appear in both fetch (list) and fetchval (count) — RLS double-guard."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)

    await pg_store.list_chunks_for_artifact(conn, "org-X", "art-1", limit=5, offset=0)

    fetch_args = conn.fetch.await_args.args
    fetchval_args = conn.fetchval.await_args.args
    # artifact_id = $1, org_id = $2 in both queries
    assert fetch_args[1] == "art-1"
    assert fetch_args[2] == "org-X"
    assert fetchval_args[1] == "art-1"
    assert fetchval_args[2] == "org-X"
