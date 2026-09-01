from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.connector_state import (
    FenceState,
    check_connector_resource_fence,
    invalidate_cache,
)


@asynccontextmanager
async def _connection_returning(row):
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=row)
    yield conn


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"current_generation": "g1", "state": "active"}, FenceState.ACTIVE),
        (
            {"current_generation": "g2", "state": "active"},
            FenceState.STALE_GENERATION,
        ),
        ({"current_generation": "g1", "state": "deleting"}, FenceState.DELETING),
        (None, FenceState.DELETED),
    ],
)
async def test_connector_resource_fence_states(row, expected) -> None:
    invalidate_cache()
    with patch(
        "knowledge_ingest.connector_state.tenant_scoped_connection",
        return_value=_connection_returning(row),
    ):
        assert await check_connector_resource_fence("connector:org-1:kb:connector-1:g1") is expected


@pytest.mark.asyncio
async def test_connector_resource_fence_fails_closed_on_lookup_error() -> None:
    invalidate_cache()

    @asynccontextmanager
    async def failing_connection():
        raise RuntimeError("database unavailable")
        yield

    with patch(
        "knowledge_ingest.connector_state.tenant_scoped_connection",
        return_value=failing_connection(),
    ):
        assert (
            await check_connector_resource_fence("connector:org-1:kb:connector-1:g1")
            is FenceState.UNKNOWN_ERROR
        )


@pytest.mark.asyncio
async def test_stale_enrichment_skips_before_chunking() -> None:
    artifact = {
        "artifact_id": "artifact-1",
        "org_id": "org-1",
        "kb_slug": "kb",
        "path": "page.md",
        "belief_time_end": 253402300800,
        "extra": {"document_text": "# Page\n\nBody"},
    }

    @asynccontextmanager
    async def admin_connection():
        yield MagicMock()

    with (
        patch(
            "knowledge_ingest.enrichment_tasks.cross_org_admin_connection",
            return_value=admin_connection(),
        ),
        patch(
            "knowledge_ingest.enrichment_tasks.pg_store.read_artifact_for_enrichment",
            new=AsyncMock(return_value=artifact),
        ),
        patch(
            "knowledge_ingest.connector_state.check_connector_resource_fence",
            new=AsyncMock(return_value=FenceState.STALE_GENERATION),
        ),
        patch("knowledge_ingest.enrichment_tasks.chunker.chunk_markdown_with_parents") as chunk,
    ):
        from knowledge_ingest.enrichment_tasks import _load_and_enrich

        await _load_and_enrich("artifact-1", "connector:org-1:kb:connector-1:old-generation")

    chunk.assert_not_called()
