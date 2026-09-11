"""A write Qdrant would reject must not first empty the document out.

Both upsert paths delete the document's existing points before building the
replacements. A vector Qdrant refuses therefore does not fail the write -- it
removes the document from the index and fails afterwards, while the artifact
still reads as ingested. Qdrant 1.19.1 rejects an empty dense vector outright
(1.19.0 accepted it), which turns a bad embedding from a broken point into a
disappeared document.
"""

from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest.qdrant_store import upsert_chunks, upsert_enriched_chunks


def _good() -> list[float]:
    return [0.1, 0.2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vectors, expected",
    [
        ([[]], "empty dense vector"),
        ([_good(), []], "empty dense vector"),
    ],
)
async def test_upsert_chunks_refuses_before_deleting(vectors, expected):
    client = AsyncMock()
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=client):
        with pytest.raises(ValueError, match=expected):
            await upsert_chunks(
                org_id="org",
                kb_slug="kb",
                path="doc.md",
                chunks=["a"] * len(vectors),
                vectors=vectors,
                artifact_id="art",
            )

    # The whole point: the existing points are still there.
    client.delete.assert_not_awaited()
    client.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_refuses_before_deleting():
    client = AsyncMock()
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=client):
        with pytest.raises(ValueError, match="empty dense vector"):
            await upsert_enriched_chunks(
                org_id="org",
                kb_slug="kb",
                path="doc.md",
                enriched_chunks=[object()],
                chunk_vectors=[[]],
                question_vectors=[None],
                artifact_id="art",
            )

    client.delete.assert_not_awaited()
    client.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_question_vector_may_be_absent_but_not_empty():
    """None means "no question vector for this chunk" and is normal; an empty
    list is a broken embedding wearing the same clothes."""
    client = AsyncMock()
    with patch("knowledge_ingest.qdrant_store.get_client", return_value=client):
        with pytest.raises(ValueError, match="question"):
            await upsert_enriched_chunks(
                org_id="org",
                kb_slug="kb",
                path="doc.md",
                enriched_chunks=[object()],
                chunk_vectors=[_good()],
                question_vectors=[[]],
                artifact_id="art",
            )

    client.delete.assert_not_awaited()
