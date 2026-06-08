"""RED: Verify quality_score and feedback_count are initialized in Qdrant payloads.

SPEC-KB-015 REQ-KB-015-16: quality_score=0.5, feedback_count=0 at ingest time.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


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
async def test_upsert_chunks_includes_parent_chunk_id(mock_qdrant_client):
    """Raw chunks should carry parent ids before enrichment completes."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_chunks

        await upsert_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            chunks=["Hello world"],
            vectors=[[0.1] * 10],
            artifact_id="art1",
            parent_chunk_ids=[42],
        )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert points[0].payload["parent_chunk_id"] == 42


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_includes_quality_fields(mock_qdrant_client):
    """upsert_enriched_chunks must set quality_score=0.5 and feedback_count=0 on every point."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_enriched_chunks

        await upsert_enriched_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            enriched_chunks=[
                SimpleNamespace(
                    original_text="Hello",
                    enriched_text="Hello enriched",
                    context_prefix="ctx",
                    questions=["What?"],
                )
            ],
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
async def test_upsert_enriched_chunks_persists_heading_path(mock_qdrant_client):
    """Heading hierarchy must stay query-time metadata, not only raw chunk text."""
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_qdrant_client):
        from knowledge_ingest.qdrant_store import upsert_enriched_chunks

        await upsert_enriched_chunks(
            org_id="org1",
            kb_slug="test-kb",
            path="/doc.md",
            enriched_chunks=[
                SimpleNamespace(
                    original_text="Admin > Mensen\n\nNodig iemand uit.",
                    enriched_text="ctx\n\nAdmin > Mensen\n\nNodig iemand uit.",
                    context_prefix="ctx",
                    questions=["Hoe nodig ik iemand uit?"],
                    heading_path="Admin > Mensen",
                )
            ],
            chunk_vectors=[[0.1] * 10],
            question_vectors=[None],
        )

        points = mock_qdrant_client.upsert.call_args.kwargs["points"]
        assert points[0].payload["heading_path"] == "Admin > Mensen"


