from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from scripts import backfill_docling_parent_chunks as script


@pytest.mark.asyncio
async def test_existing_parent_chunks_repair_qdrant_payloads() -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[{"id": 10}, {"id": 11}])
    points = [
        {"id": "point-1", "payload": {"text": "A", "chunk_index": 0}},
        {"id": "point-2", "payload": {"text": "B", "chunk_index": 1}},
    ]

    with (
        patch.object(script, "_qdrant_scroll", return_value=points),
        patch.object(script, "_qdrant_set_parent_chunk_ids") as set_payload,
    ):
        inserted, status = await script._backfill_one_artifact(
            conn,
            "org1",
            "kb1",
            "artifact-1",
        )

    assert inserted == 0
    assert status == "qdrant_parent_ids_repaired"
    set_payload.assert_called_once_with({"point-1": 10, "point-2": 11})


@pytest.mark.asyncio
async def test_existing_parent_chunks_skip_when_qdrant_already_matches() -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[{"id": 10}, {"id": 11}])
    points = [
        {"id": "point-1", "payload": {"text": "A", "chunk_index": 0, "parent_chunk_id": 10}},
        {"id": "point-2", "payload": {"text": "B", "chunk_index": 1, "parent_chunk_id": 11}},
    ]

    with (
        patch.object(script, "_qdrant_scroll", return_value=points),
        patch.object(script, "_qdrant_set_parent_chunk_ids") as set_payload,
    ):
        inserted, status = await script._backfill_one_artifact(
            conn,
            "org1",
            "kb1",
            "artifact-1",
        )

    assert inserted == 0
    assert status == "already_repaired"
    set_payload.assert_not_called()


@pytest.mark.asyncio
async def test_docling_artifact_listing_includes_already_parented_rows() -> None:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"org_id": "org1", "kb_slug": "kb1", "artifact_id": "a1"}])

    result = await script._list_docling_artifacts_without_parents(conn)

    assert result == [("org1", "kb1", "a1")]
    sql = conn.fetch.await_args.args[0]
    assert "LEFT JOIN knowledge.parent_chunks" not in sql
    assert "HAVING COUNT" not in sql
