"""Sparse vectors at the base ingest, enrichment in batches, and the backfill.

Qdrant runs in-memory, so the tests read back what a point really carries.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

import knowledge_ingest.qdrant_store as qs
from knowledge_ingest.enrichment import EnrichedChunk
from knowledge_ingest.scripts import sparse_backfill

DIM = 4
ORG, KB, PATH, ART = "org-test", "kb-test", "docs/big.md", "art-1"


@pytest.fixture
async def mem_client():
    client = AsyncQdrantClient(":memory:")
    await client.create_collection(
        qs.COLLECTION,
        vectors_config={
            "vector_chunk": VectorParams(size=DIM, distance=Distance.COSINE),
            "vector_questions": VectorParams(size=DIM, distance=Distance.COSINE),
        },
        sparse_vectors_config={"vector_sparse": SparseVectorParams(index=SparseIndexParams())},
    )
    with patch.object(qs, "get_client", return_value=client):
        yield client
    await client.close()


def _sparse_for(texts: list[str]) -> list[SparseVector | None]:
    return [SparseVector(indices=[len(t)], values=[1.0]) for t in texts]


async def _write_raw(texts: list[str], *, sparse: bool = True) -> None:
    embed = AsyncMock(side_effect=_sparse_for if sparse else lambda t: [None] * len(t))
    with patch.object(qs.sparse_embedder, "embed_sparse_batch", embed):
        await qs.upsert_chunks(
            org_id=ORG,
            kb_slug=KB,
            path=PATH,
            chunks=texts,
            vectors=[[1.0, 0.0, 0.0, 0.0]] * len(texts),
            artifact_id=ART,
            parent_chunk_ids=list(range(100, 100 + len(texts))),
        )


async def _points(client: AsyncQdrantClient) -> list:
    points, _ = await client.scroll(qs.COLLECTION, limit=100, with_payload=True, with_vectors=True)
    return sorted(points, key=lambda p: p.payload.get("chunk_index", 0))


# --- sparse at the base -----------------------------------------------------


async def test_raw_chunk_carries_sparse_vector_on_first_write(mem_client):
    await _write_raw(["alpha text", "beta"])

    points = await _points(mem_client)
    assert [p.payload["text"] for p in points] == ["alpha text", "beta"]
    assert all("context_prefix" not in p.payload for p in points)
    assert [p.vector["vector_sparse"].indices for p in points] == [[10], [4]]


async def test_raw_chunk_stays_dense_only_when_sidecar_gives_nothing(mem_client):
    await _write_raw(["alpha"], sparse=False)

    (point,) = await _points(mem_client)
    assert "vector_sparse" not in point.vector


# --- enrichment in batches --------------------------------------------------


def _enriched(text: str) -> EnrichedChunk:
    return EnrichedChunk(text, f"ctx\n\n{text}", "ctx", ["q?"])


async def test_enriched_batch_replaces_only_its_own_chunks(mem_client):
    await _write_raw(["c0", "c1", "c2", "c3", "c4"])

    await qs.upsert_enriched_chunks(
        org_id=ORG,
        kb_slug=KB,
        path=PATH,
        enriched_chunks=[_enriched("c2"), _enriched("c3")],
        chunk_vectors=[[0.0, 1.0, 0.0, 0.0]] * 2,
        question_vectors=[None, None],
        sparse_vectors=_sparse_for(["ctx c2", "ctx c3"]),
        artifact_id=ART,
        parent_chunk_ids=[102, 103],
        chunk_offset=2,
    )

    points = await _points(mem_client)
    assert [p.payload["chunk_index"] for p in points] == [0, 1, 2, 3, 4]
    assert [bool(p.payload.get("context_prefix")) for p in points] == [
        False,
        False,
        True,
        True,
        False,
    ]
    assert [p.payload["parent_chunk_id"] for p in points] == [100, 101, 102, 103, 104]
    assert all("vector_sparse" in p.vector for p in points)


async def test_enriched_batch_leaves_points_of_another_artifact_version_alone(mem_client):
    await _write_raw(["c0", "c1"])  # artifact "art-1"
    await qs.upsert_enriched_chunks(
        org_id=ORG,
        kb_slug=KB,
        path=PATH,
        enriched_chunks=[_enriched("c0")],
        chunk_vectors=[[0.0, 1.0, 0.0, 0.0]],
        question_vectors=[None],
        artifact_id="art-OLD",
        chunk_offset=0,
    )

    points = await _points(mem_client)
    assert sorted((p.payload["artifact_id"], p.payload["chunk_index"]) for p in points) == [
        ("art-1", 0),
        ("art-1", 1),
        ("art-OLD", 0),
    ]


def _artifact(extra: dict | None = None) -> dict:
    return {
        "artifact_id": ART,
        "org_id": ORG,
        "kb_slug": KB,
        "path": PATH,
        "user_id": None,
        "content_type": "document",
        "synthesis_depth": 0,
        "belief_time_end": 253402300800,
        "extra": extra or {"document_text": "Preview only", "document_text_truncated": True},
    }


async def _run_batch(
    offset: int, size: int, extra: dict | None = None
) -> tuple[AsyncMock, MagicMock]:
    from knowledge_ingest import enrichment_tasks

    enrich = AsyncMock(return_value=True)
    bulk = MagicMock()
    bulk.configure.return_value.defer_async = AsyncMock()
    app = MagicMock(enrich_document_bulk=bulk)
    with (
        patch.object(
            enrichment_tasks.pg_store,
            "read_artifact_for_enrichment",
            AsyncMock(return_value=_artifact(extra)),
        ),
        patch.object(enrichment_tasks, "enrichment_batch_size", return_value=size),
        patch.object(enrichment_tasks, "_enrich_document", enrich),
        patch.object(enrichment_tasks, "get_app", return_value=app),
        patch.object(enrichment_tasks, "cross_org_admin_connection"),
    ):
        await enrichment_tasks._load_and_enrich(ART, batch_offset=offset)
    return enrich, bulk


async def test_document_above_batch_size_is_enriched_in_queued_batches(mem_client):
    await _write_raw([f"chunk {i}" for i in range(5)])

    enrich, bulk = await _run_batch(offset=0, size=2)
    kwargs = enrich.await_args.kwargs
    assert kwargs["chunks"] == ["chunk 0", "chunk 1"]
    assert kwargs["chunk_offset"] == 0
    assert kwargs["existing_parent_chunk_ids"] == [100, 101]
    bulk.configure.return_value.defer_async.assert_awaited_once_with(
        artifact_id=ART, batch_offset=2
    )

    enrich, bulk = await _run_batch(offset=4, size=2)
    assert enrich.await_args.kwargs["chunks"] == ["chunk 4"]
    bulk.configure.assert_not_called()


# --- backfill and count -----------------------------------------------------


async def test_backfill_dry_run_counts_and_real_run_writes_only_missing(mem_client):
    await _write_raw(["second"], sparse=False)  # a base chunk written before this change
    await mem_client.upsert(
        qs.COLLECTION,
        points=[
            PointStruct(
                id="00000000-0000-0000-0000-000000000001",
                vector={
                    "vector_chunk": [0, 1, 0, 0],
                    "vector_sparse": SparseVector(indices=[1], values=[1.0]),
                },
                payload={"org_id": ORG, "kb_slug": KB, "path": "p2", "text": "kept"},
            ),
            PointStruct(
                id="00000000-0000-0000-0000-000000000002",
                vector={"vector_chunk": [0, 1, 0, 0]},
                payload={
                    "org_id": ORG,
                    "kb_slug": KB,
                    "path": "p3",
                    "text": "raw",
                    "text_enriched": "ctx\n\nraw",
                    "context_prefix": "ctx",
                    "questions": ["q?"],
                },
            ),
        ],
    )
    before = (await sparse_backfill.count_chunks(mem_client, ORG))[KB]
    assert (before["chunks"], before["sparse"], before["no_sparse"]) == (3, 1, 2)
    assert (before["context"], before["questions"]) == (1, 1)

    embed = AsyncMock(side_effect=_sparse_for)
    with patch.object(sparse_backfill.sparse_embedder, "embed_sparse_batch", embed):
        dry = await sparse_backfill.backfill_sparse(mem_client, ORG, execute=False)
        assert dry["missing"] == 2 and dry["written"] == 0
        embed.assert_not_awaited()

        done = await sparse_backfill.backfill_sparse(mem_client, ORG, execute=True)
        assert (done["written"], done["failed"]) == (2, 0)
        # The enriched chunk is embedded as its enriched text, the raw one as its text.
        assert sorted(t for call in embed.await_args_list for t in call.args[0]) == [
            "ctx\n\nraw",
            "second",
        ]

        again = await sparse_backfill.backfill_sparse(mem_client, ORG, execute=True)
        assert again["missing"] == 0

    after = (await sparse_backfill.count_chunks(mem_client, ORG))[KB]
    assert (after["sparse"], after["no_sparse"]) == (3, 0)
    # Writing a sparse vector leaves the dense vector and the payload alone.
    kept = {p.payload["text"]: p for p in await _points(mem_client)}
    assert kept["raw"].vector["vector_chunk"] is not None
    assert kept["raw"].payload["context_prefix"] == "ctx"


async def test_prechunked_document_routes_on_its_points_not_on_a_rechunk_of_its_text(mem_client):
    """250 connector chunks whose stored text re-chunks to one must not be replaced."""
    await _write_raw([f"connector chunk {i}" for i in range(250)])

    enrich, bulk = await _run_batch(offset=0, size=200, extra={"document_text": "One short text."})

    kwargs = enrich.await_args.kwargs
    assert kwargs["chunk_offset"] == 0
    assert kwargs["chunks"] == [f"connector chunk {i}" for i in range(200)]
    bulk.configure.return_value.defer_async.assert_awaited_once_with(
        artifact_id=ART, batch_offset=200
    )


async def test_sparse_result_count_mismatch_is_a_loud_error_not_a_silent_drop(mem_client):
    short = AsyncMock(return_value=_sparse_for(["only one"]))
    with patch.object(qs.sparse_embedder, "embed_sparse_batch", short):
        with pytest.raises(ValueError):
            await qs.upsert_chunks(
                org_id=ORG,
                kb_slug=KB,
                path=PATH,
                chunks=["a", "b"],
                vectors=[[1.0, 0.0, 0.0, 0.0]] * 2,
                artifact_id=ART,
            )


async def _fail_enrichment(chunk_offset: int | None) -> AsyncMock:
    from knowledge_ingest import enrichment_tasks

    status = AsyncMock()
    with (
        patch.object(enrichment_tasks, "_set_direct_upload_index_status", status),
        patch.object(
            enrichment_tasks.enrichment, "enrich_chunks", AsyncMock(side_effect=RuntimeError("x"))
        ),
    ):
        written = await enrichment_tasks._enrich_document(
            org_id=ORG,
            kb_slug=KB,
            path=PATH,
            document_text="body",
            chunks=["body"],
            title="T",
            artifact_id=ART,
            user_id=None,
            extra_payload={"document_summary": "s", "document_language": "en"},
            synthesis_depth=0,
            chunk_offset=chunk_offset,
        )
    assert written is False
    return status


async def test_failed_later_batch_leaves_the_document_status_alone():
    status = await _fail_enrichment(chunk_offset=200)
    status.assert_not_awaited()


async def test_failed_first_batch_or_whole_document_still_sets_failed():
    for offset in (0, None):
        status = await _fail_enrichment(chunk_offset=offset)
        assert status.await_args.args[1] == "failed"
