"""A write Qdrant would reject, or that fails, must not empty the document out.

Both upsert paths replace a document's existing points. When the delete ran
first, a write that then failed removed the document from the index while
the artifact still read as ingested. Qdrant 1.19.1 rejects an empty dense
vector outright (1.19.0 accepted it), so a bad embedding is refused before
Qdrant is touched, and any other failed upsert leaves the old points alone.
"""

from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, SparseIndexParams, SparseVectorParams, VectorParams

import knowledge_ingest.qdrant_store as qs
from knowledge_ingest.enrichment import EnrichedChunk
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


# --- a write Qdrant fails must not have emptied the document first -----------
#
# Production, 2026-09-21: the enrichment of a synced support article deleted
# the article's points, the upsert that followed did not land, and
# _enrich_document swallowed the error as an infra failure on the assumption
# that the raw vectors remain. They did not: the artifact stayed
# index_status=synced with zero points, and the nightly PG/Qdrant reconcile
# reported it missing for 30 days.

_DIM = 4
_ORG, _KB, _PATH, _ART = "org-test", "kb-test", "docs/a.md", "art-1"


@pytest.fixture
async def mem_client():
    client = AsyncQdrantClient(":memory:")
    await client.create_collection(
        qs.COLLECTION,
        vectors_config={
            "vector_chunk": VectorParams(size=_DIM, distance=Distance.COSINE),
            "vector_questions": VectorParams(size=_DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={"vector_sparse": SparseVectorParams(index=SparseIndexParams())},
    )
    no_sparse = AsyncMock(side_effect=lambda texts: [None] * len(texts))
    with (
        patch.object(qs, "get_client", return_value=client),
        patch.object(qs.sparse_embedder, "embed_sparse_batch", no_sparse),
    ):
        yield client
    await client.close()


async def _texts(client: AsyncQdrantClient) -> list[str]:
    points, _ = await client.scroll(qs.COLLECTION, limit=100, with_payload=True)
    return sorted(p.payload["text"] for p in points)


async def _write_raw(texts: list[str], artifact_id: str = _ART) -> None:
    await upsert_chunks(
        org_id=_ORG,
        kb_slug=_KB,
        path=_PATH,
        chunks=texts,
        vectors=[[1.0, 0.0, 0.0, 0.0]] * len(texts),
        artifact_id=artifact_id,
    )


async def _write_enriched(texts: list[str]) -> None:
    await upsert_enriched_chunks(
        org_id=_ORG,
        kb_slug=_KB,
        path=_PATH,
        enriched_chunks=[EnrichedChunk(t, f"ctx\n\n{t}", "ctx", []) for t in texts],
        chunk_vectors=[[0.0, 1.0, 0.0, 0.0]] * len(texts),
        question_vectors=[None] * len(texts),
        artifact_id=_ART,
    )


def _failing_upsert(client: AsyncQdrantClient):
    return patch.object(client, "upsert", AsyncMock(side_effect=RuntimeError("upsert timed out")))


@pytest.mark.asyncio
async def test_failed_enriched_upsert_keeps_the_documents_points(mem_client):
    await _write_raw(["raw 0", "raw 1"])

    with _failing_upsert(mem_client), pytest.raises(RuntimeError):
        await _write_enriched(["enriched 0"])

    assert await _texts(mem_client) == ["raw 0", "raw 1"]


@pytest.mark.asyncio
async def test_failed_raw_upsert_keeps_the_documents_points(mem_client):
    await _write_raw(["old 0", "old 1"], artifact_id="art-old")

    with _failing_upsert(mem_client), pytest.raises(RuntimeError):
        await _write_raw(["new 0"], artifact_id="art-new")

    assert await _texts(mem_client) == ["old 0", "old 1"]


@pytest.mark.asyncio
async def test_successful_replace_leaves_only_the_new_points(mem_client):
    await _write_raw(["old 0", "old 1", "old 2"], artifact_id="art-old")
    await _write_raw(["raw 0", "raw 1"])
    await _write_enriched(["enriched 0"])

    assert await _texts(mem_client) == ["enriched 0"]


@pytest.mark.asyncio
async def test_two_concurrent_writes_of_one_document_never_leave_it_empty(mem_client):
    """No lock serialises writers of one document: an ingest of a new version
    and an enrichment, or two enrichment jobs under different queueing locks,
    can run at once. Point ids are random uuid4s. Each Qdrant call waits until
    both writers have made it, so both upserts land before either delete, the
    worst interleaving."""
    import asyncio

    await _write_raw(["old 0"], artifact_id="art-old")
    real_upsert, real_delete = mem_client.upsert, mem_client.delete
    upserts, deletes = asyncio.Barrier(2), asyncio.Barrier(2)

    async def upsert(*args, **kwargs):
        await upserts.wait()
        return await real_upsert(*args, **kwargs)

    async def delete(*args, **kwargs):
        await deletes.wait()
        return await real_delete(*args, **kwargs)

    with (
        patch.object(mem_client, "upsert", side_effect=upsert),
        patch.object(mem_client, "delete", side_effect=delete),
    ):
        await asyncio.gather(
            _write_raw(["a 0", "a 1"], artifact_id="art-a"),
            _write_raw(["b 0", "b 1"], artifact_id="art-b"),
        )

    texts = await _texts(mem_client)
    assert {"a 0", "a 1"} <= set(texts) or {"b 0", "b 1"} <= set(texts), texts
    assert "old 0" not in texts
