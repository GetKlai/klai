"""
Tests for retrieve endpoint — PostgreSQL artifact metadata enrichment.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_qdrant_result(artifact_id: str | None = "abc-123", extra: dict | None = None):
    r = MagicMock()
    payload = {
        "text": "Test chunk text",
        "kb_slug": "org",
        "path": "doc.md",
        "org_id": "org1",
        "chunk_index": 0,
        "title": "Test Doc",
    }
    if artifact_id:
        payload["artifact_id"] = artifact_id
    if extra:
        payload.update(extra)
    r.payload = payload
    r.score = 0.87
    return r


def _make_pg_row(artifact_id: str = "abc-123"):
    row = {
        "id": artifact_id,
        "provenance_type": "observed",
        "assertion_mode": "factual",
        "synthesis_depth": 2,
        "confidence": "high",
        "belief_time_start": 1705276800,
        "belief_time_end": 253402300800,
    }
    return row


@pytest.mark.asyncio
async def test_retrieve_enriches_with_pg_metadata():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_make_pg_row("abc-123")])

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client, \
         patch("knowledge_ingest.routes.retrieve.get_pool", new_callable=AsyncMock, return_value=pool), \
         patch("knowledge_ingest.embedder.embed_one", new_callable=AsyncMock, return_value=[0.1] * 1024):

        mock_client.return_value.search = AsyncMock(return_value=[_make_qdrant_result("abc-123")])

        from knowledge_ingest.routes.retrieve import retrieve
        from knowledge_ingest.models import RetrieveRequest

        req = RetrieveRequest(org_id="org1", query="test", kb_slugs=["org"])
        response = await retrieve(req)

    assert len(response.chunks) == 1
    chunk = response.chunks[0]
    assert chunk.artifact_id == "abc-123"
    assert chunk.provenance_type == "observed"
    assert chunk.assertion_mode == "factual"
    assert chunk.synthesis_depth == 2
    assert chunk.confidence == "high"


@pytest.mark.asyncio
async def test_retrieve_without_artifact_id_skips_pg_lookup():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client, \
         patch("knowledge_ingest.routes.retrieve.get_pool", new_callable=AsyncMock, return_value=pool), \
         patch("knowledge_ingest.embedder.embed_one", new_callable=AsyncMock, return_value=[0.1] * 1024):

        mock_client.return_value.search = AsyncMock(return_value=[_make_qdrant_result(None)])

        from knowledge_ingest.routes.retrieve import retrieve
        from knowledge_ingest.models import RetrieveRequest

        req = RetrieveRequest(org_id="org1", query="test")
        response = await retrieve(req)

    pool.fetch.assert_not_called()
    chunk = response.chunks[0]
    assert chunk.artifact_id is None
    assert chunk.assertion_mode is None


@pytest.mark.asyncio
async def test_retrieve_deduplicates_artifact_ids_for_pg_query():
    """Multiple chunks from same document should result in ONE pg fetch, not N."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_make_pg_row("same-id")])

    qdrant_results = [_make_qdrant_result("same-id") for _ in range(5)]

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client, \
         patch("knowledge_ingest.routes.retrieve.get_pool", new_callable=AsyncMock, return_value=pool), \
         patch("knowledge_ingest.embedder.embed_one", new_callable=AsyncMock, return_value=[0.1] * 1024):

        mock_client.return_value.search = AsyncMock(return_value=qdrant_results)

        from knowledge_ingest.routes.retrieve import retrieve
        from knowledge_ingest.models import RetrieveRequest

        req = RetrieveRequest(org_id="org1", query="test", top_k=5)
        await retrieve(req)

    pool.fetch.assert_called_once()
    fetched_ids = pool.fetch.call_args[0][1]
    assert len(fetched_ids) == 1  # deduplicated to single ID


@pytest.mark.asyncio
async def test_retrieve_returns_correct_source_and_score():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[_make_pg_row("abc-123")])

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client, \
         patch("knowledge_ingest.routes.retrieve.get_pool", new_callable=AsyncMock, return_value=pool), \
         patch("knowledge_ingest.embedder.embed_one", new_callable=AsyncMock, return_value=[0.1] * 1024):

        mock_client.return_value.search = AsyncMock(return_value=[_make_qdrant_result("abc-123")])

        from knowledge_ingest.routes.retrieve import retrieve
        from knowledge_ingest.models import RetrieveRequest

        response = await retrieve(RetrieveRequest(org_id="org1", query="test"))

    chunk = response.chunks[0]
    assert chunk.source == "org/doc.md"
    assert chunk.score == pytest.approx(0.87)
    assert chunk.text == "Test chunk text"
