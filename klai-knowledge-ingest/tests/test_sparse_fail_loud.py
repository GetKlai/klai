"""A chunk is never written without its sparse vector.

The sparse sidecar runs on the GPU server, which restarts for updates. While it
was down, ingest wrote chunks with their dense vector only and carried on, so
hybrid search found them through one leg for good: on 2026-10-29 one enrichment
wrote ten such chunks. Now the write is refused, the job is retried for longer
than a restart of that server takes, and then the document records failed.

The sidecar is a real HTTP exchange here (httpx MockTransport), not a stubbed
function, so the tests cover the embedder's own failure handling too.
"""

from __future__ import annotations

import datetime
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    MatchValue,
    PointStruct,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

import knowledge_ingest.qdrant_store as qs
from knowledge_ingest import enrichment_tasks, sparse_embedder
from knowledge_ingest.enrichment import EnrichedChunk
from knowledge_ingest.sparse_embedder import SparseEmbeddingUnavailable

pytestmark = pytest.mark.real_sparse_embedder

DIM = 4
ORG, KB, PATH, ART = "org-test", "kb-test", "docs/a.md", "art-1"
CHUNKS = ["alpha text", "beta"]
_DOWN = 10**6  # down for the whole test


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


@pytest.fixture
def sidecar():
    """The sidecar behind the embedder's HTTP client; it refuses the next ``down_for`` calls."""
    state = SimpleNamespace(down_for=0, calls=0)

    def _answer(request: httpx.Request) -> httpx.Response:
        state.calls += 1
        if state.down_for:
            state.down_for -= 1
            raise httpx.ConnectError("connection refused", request=request)
        texts = json.loads(request.content)["texts"]
        return httpx.Response(
            200, json={"results": [{"indices": [len(t)], "values": [1.0]} for t in texts]}
        )

    transport = httpx.MockTransport(_answer)
    real_client = httpx.AsyncClient
    with (
        patch.object(
            sparse_embedder.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        ),
        patch.object(sparse_embedder.settings, "sparse_sidecar_url", "http://sparse.example.com"),
        patch.object(sparse_embedder.asyncio, "sleep", AsyncMock()),
    ):
        yield state


async def _write_raw() -> None:
    await qs.upsert_chunks(
        org_id=ORG,
        kb_slug=KB,
        path=PATH,
        chunks=CHUNKS,
        vectors=[[1.0, 0.0, 0.0, 0.0]] * len(CHUNKS),
        sparse_vectors=await sparse_embedder.embed_sparse_batch(CHUNKS),
        artifact_id=ART,
    )


async def _points(client: AsyncQdrantClient) -> list:
    points, _ = await client.scroll(qs.COLLECTION, limit=100, with_payload=True, with_vectors=True)
    return sorted(points, key=lambda p: p.payload["chunk_index"])


class _App:
    def __init__(self) -> None:
        self.tasks: dict = {}
        self.retry: dict = {}

    def task(self, **kwargs):
        def _decorator(fn):
            self.tasks[fn.__name__] = fn
            self.retry[fn.__name__] = kwargs.get("retry")
            return fn

        return _decorator


async def _load_and_enrich(artifact_id: str, batch_offset: int = 0) -> None:
    await enrichment_tasks._enrich_document(
        org_id=ORG,
        kb_slug=KB,
        path=PATH,
        document_text="alpha text beta",
        chunks=CHUNKS,
        title="T",
        artifact_id=artifact_id,
        user_id=None,
        extra_payload={"document_summary": "s", "document_language": "en"},
        synthesis_depth=3,
    )


async def _run_enrichment(attempts: int, finish: AsyncMock) -> None:
    """Run the bulk enrichment job as its ``attempts``-th retry; ``finish`` records the state."""
    app = _App()
    enrichment_tasks._register_tasks(app)

    async def _enrich(**kw):
        return [EnrichedChunk(c, f"ctx {c}", "ctx", []) for c in kw["chunks"]]

    with (
        patch.object(enrichment_tasks, "_load_and_enrich", _load_and_enrich),
        patch.object(enrichment_tasks, "_finish", finish),
        patch.object(enrichment_tasks, "_link_graph_fields", AsyncMock()),
        patch.object(enrichment_tasks, "_still_current_for_write", AsyncMock(return_value=True)),
        patch.object(enrichment_tasks.enrichment, "enrich_chunks", _enrich),
        patch.object(
            enrichment_tasks.embedder,
            "embed",
            AsyncMock(side_effect=lambda t: [[0.0, 1.0, 0.0, 0.0]] * len(t)),
        ),
        patch.object(enrichment_tasks.kb_config, "get_kb_aliases", AsyncMock(return_value=[])),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_product_rules", AsyncMock(return_value=[])
        ),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_visibility", AsyncMock(return_value="internal")
        ),
    ):
        context = SimpleNamespace(job=SimpleNamespace(attempts=attempts, queueing_lock="lock"))
        await app.tasks["enrich_document_bulk"](context, artifact_id=ART)


def _retry_decision(exc: BaseException, attempts: int):
    app = _App()
    enrichment_tasks._register_tasks(app)
    job = SimpleNamespace(attempts=attempts)
    return app.retry["enrich_document_bulk"].get_retry_decision(exception=exc, job=job)


async def test_sparse_outage_in_enrichment_writes_nothing_retries_then_fails(
    mem_client, sidecar, real_procrastinate
):
    await _write_raw()
    before = await _points(mem_client)
    sidecar.down_for = _DOWN

    # Attempts up to the sparse bound leave the document pending and are
    # retried, including attempts past the ordinary bulk retry budget.
    finish = AsyncMock()
    for attempts in (0, enrichment_tasks._BULK_MAX_ATTEMPTS):
        with pytest.raises(SparseEmbeddingUnavailable) as raised:
            await _run_enrichment(attempts, finish)
        finish.assert_not_awaited()
        decision = _retry_decision(raised.value, attempts)
        assert decision is not None
        wait = decision.retry_at - datetime.datetime.now(datetime.UTC)
        assert wait.total_seconds() == pytest.approx(enrichment_tasks._SPARSE_RETRY_SECONDS, abs=5)

    # The raw points are exactly as they were: no dense-only enriched write.
    after = await _points(mem_client)
    assert [p.vector["vector_sparse"] for p in after] == [p.vector["vector_sparse"] for p in before]
    assert all("context_prefix" not in p.payload for p in after)

    # The retries span comfortably more than the longest measured restart (6 min).
    span = enrichment_tasks._SPARSE_RETRY_SECONDS * enrichment_tasks._SPARSE_MAX_ATTEMPTS
    assert span >= 15 * 60

    last = enrichment_tasks._SPARSE_MAX_ATTEMPTS
    with pytest.raises(SparseEmbeddingUnavailable) as raised:
        await _run_enrichment(last, finish)
    finish.assert_awaited_once_with("enrichment", ART, "failed", "sparse_unavailable")
    assert _retry_decision(raised.value, last) is None


async def test_sparse_back_within_the_bound_writes_both_vectors(mem_client, sidecar):
    await _write_raw()
    sidecar.down_for = _DOWN
    finish = AsyncMock()
    with pytest.raises(SparseEmbeddingUnavailable):
        await _run_enrichment(0, finish)

    sidecar.down_for = 0
    await _run_enrichment(1, finish)

    finish.assert_awaited_once_with("enrichment", ART, "done")
    points = await _points(mem_client)
    assert [p.payload["context_prefix"] for p in points] == ["ctx", "ctx"]
    assert [p.vector["vector_sparse"].indices for p in points] == [
        [len("ctx alpha text")],
        [len("ctx beta")],
    ]
    assert all(p.vector["vector_chunk"] for p in points)


async def test_a_short_sidecar_blip_is_ridden_out_inside_one_call(sidecar):
    sidecar.down_for = 1

    vectors = await sparse_embedder.embed_sparse_batch(["abc"])

    assert [v.indices for v in vectors] == [[3]]
    assert sidecar.calls == 2


async def test_base_ingest_with_the_sidecar_down_creates_no_version(sidecar):
    """The base ingest embeds sparse before it records a version, like dense, so
    an outage fails the request with nothing written and a retry ingests again."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document
    from tests.test_enrichment_dedup import _base_patches, _make_mock_app, _make_mock_conn

    sidecar.down_for = _DOWN
    mock_app, _, _ = _make_mock_app()
    req = IngestRequest(
        org_id="org-1",
        kb_slug="kb",
        path="docs/page.md",
        content="# Title\n\nContent.",
        source_type="docs",
        content_type="kb_article",
    )
    async with _base_patches(mock_app):
        with (
            patch("knowledge_ingest.routes.ingest.pg_store.create_artifact", AsyncMock()) as create,
            patch("knowledge_ingest.routes.ingest.qdrant_store.upsert_chunks", AsyncMock()) as up,
            pytest.raises(SparseEmbeddingUnavailable),
        ):
            await ingest_document(_make_mock_conn(), req)

    create.assert_not_awaited()
    up.assert_not_awaited()


async def test_storage_refuses_a_point_without_its_sparse_vector(mem_client, sidecar):
    """The one function that writes points checks it, so no caller can skip it."""
    await _write_raw()
    before = await _points(mem_client)
    dense_only = PointStruct(id=str(uuid.uuid4()), vector={"vector_chunk": [1.0, 0, 0, 0]})

    with pytest.raises(ValueError, match="without a sparse vector"):
        await qs._replace_points(
            mem_client, [FieldCondition(key="path", match=MatchValue(value=PATH))], [dense_only]
        )

    assert [p.id for p in await _points(mem_client)] == [p.id for p in before]
