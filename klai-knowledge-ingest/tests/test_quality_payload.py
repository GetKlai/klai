"""RED: Verify quality_score and feedback_count are initialized in Qdrant payloads.

SPEC-KB-015 REQ-KB-015-16: quality_score=0.5, feedback_count=0 at ingest time.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from qdrant_client.models import PointStruct


@pytest.fixture
def mock_qdrant_client():
    client = AsyncMock()
    client.upsert = AsyncMock()
    client.delete = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_upsert_chunks_includes_quality_fields(mock_qdrant_client):
    """upsert_chunks must set quality_score=0.5 and feedback_count=0 on every point."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_chunks

        chunks = ["Hello world"]
        vectors = [[0.1] * 10]

        await upsert_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            chunks=chunks,
            vectors=vectors,
            artifact_id="art1",
        )

        mock_qdrant_client.upsert.assert_called_once()
        call_args = mock_qdrant_client.upsert.call_args
        points = call_args.kwargs.get("points") or call_args[1].get("points") or call_args[0][1]

        assert len(points) == 1
        payload = points[0].payload
        assert payload["quality_score"] == 0.5, "quality_score must be initialized to 0.5"
        assert payload["feedback_count"] == 0, "feedback_count must be initialized to 0"


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_includes_quality_fields(mock_qdrant_client):
    """upsert_enriched_chunks must set quality_score=0.5 and feedback_count=0 on every point."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_enriched_chunks

        # Minimal enriched chunk mock
        class FakeEnrichedChunk:
            original_text = "Hello"
            enriched_text = "Hello enriched"
            context_prefix = "ctx"
            questions = ["What?"]

        await upsert_enriched_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            enriched_chunks=[FakeEnrichedChunk()],
            chunk_vectors=[[0.1] * 10],
            question_vectors=[None],
        )

        mock_qdrant_client.upsert.assert_called_once()
        call_args = mock_qdrant_client.upsert.call_args
        points = call_args.kwargs.get("points") or call_args[1].get("points") or call_args[0][1]

        assert len(points) == 1
        payload = points[0].payload
        assert payload["quality_score"] == 0.5, "quality_score must be initialized to 0.5"
        assert payload["feedback_count"] == 0, "feedback_count must be initialized to 0"


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_persists_chunk_type(mock_qdrant_client):
    """SPEC-KB-021: LLM-classified chunk_type must reach the Qdrant payload."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_enriched_chunks

        class FakeEnrichedChunk:
            original_text = "Stap 1: open de app."
            enriched_text = "ctx\n\nStap 1: open de app."
            context_prefix = "ctx"
            questions = ["Hoe open ik de app?"]
            chunk_type = "procedural"

        await upsert_enriched_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            enriched_chunks=[FakeEnrichedChunk()],
            chunk_vectors=[[0.1] * 10],
            question_vectors=[None],
        )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert points[0].payload["chunk_type"] == "procedural"
        # Document-level content_type default is preserved.
        assert points[0].payload["content_type"] == "unknown"


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_omits_chunk_type_when_missing(mock_qdrant_client):
    """Pre-enrichment fast path: chunk_type absent on point, not an empty string."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_enriched_chunks

        class FakeEnrichedChunk:
            original_text = "Hello"
            enriched_text = "Hello enriched"
            context_prefix = "ctx"
            questions = ["What?"]
            # no chunk_type attribute set

        await upsert_enriched_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            enriched_chunks=[FakeEnrichedChunk()],
            chunk_vectors=[[0.1] * 10],
            question_vectors=[None],
        )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert "chunk_type" not in points[0].payload
