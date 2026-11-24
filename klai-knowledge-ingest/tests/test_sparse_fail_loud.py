"""A chunk is never written without its vectors, and an embedding outage is waited out.

The dense and sparse embedding services run on the GPU server, which restarts
for updates. While the sparse sidecar was down, ingest wrote chunks with their
dense vector only and carried on, so hybrid search found them through one leg
for good: on 2026-10-29 one enrichment wrote ten such chunks. Now the write is
refused, the job is queued again for longer than a restart of that server
takes, without spending its ordinary retries, and then the document records
failed. A health call to both services before the model work keeps a retry
during an outage from paying for that work again.

Both services are a real HTTP exchange here (httpx MockTransport), so the
tests cover the embedders' own failure handling too.
"""

from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from knowledge_ingest import embedder, enrichment_tasks, sparse_embedder
from knowledge_ingest.embedder import (
    EMBEDDING_OUTAGE_WINDOW_SECONDS,
    EMBEDDING_RETRY_SECONDS,
    EmbeddingUnavailable,
)
from knowledge_ingest.enrichment import EnrichedChunk

pytestmark = pytest.mark.real_sparse_embedder

DIM = 4
ORG, KB, PATH, ART = "org-test", "kb-test", "docs/a.md", "art-1"
CHUNKS = ["alpha text", "beta"]
_DOWN = 10**6  # down for the whole test
_DENSE, _SPARSE = "dense.example.com", "sparse.example.com"


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
def services():
    """Both embedding services behind one HTTP transport.

    ``down[host]`` refuses that many calls to its embed endpoint;
    ``health_down`` lists the hosts whose health check fails too (a restart),
    and ``sparse_status`` makes the sidecar answer with that status instead.
    """
    state = SimpleNamespace(
        down={_DENSE: 0, _SPARSE: 0},
        health_down=set(),
        health_status={},
        sparse_status=200,
        sparse_calls=0,
    )

    def _answer(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if request.url.path == "/health":
            if host in state.health_down:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(state.health_status.get(host, 200))
        if host == _SPARSE:
            state.sparse_calls += 1
        if state.down[host]:
            state.down[host] -= 1
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content)
        if host == _DENSE:
            data = [
                {"index": i, "embedding": [0.0, 1.0, 0.0, 0.0]} for i in range(len(body["input"]))
            ]
            return httpx.Response(200, json={"data": data})
        if state.sparse_status != 200:
            return httpx.Response(state.sparse_status)
        results = [{"indices": [len(t)], "values": [1.0]} for t in body["texts"]]
        return httpx.Response(200, json={"results": results})

    transport = httpx.MockTransport(_answer)
    real_client = httpx.AsyncClient

    with (
        patch.object(
            sparse_embedder.httpx,
            "AsyncClient",
            lambda **kwargs: real_client(transport=transport, **kwargs),
        ),
        patch.object(embedder.settings, "tei_url", f"http://{_DENSE}"),
        patch.object(sparse_embedder.settings, "sparse_sidecar_url", f"http://{_SPARSE}"),
        patch.object(sparse_embedder.asyncio, "sleep", AsyncMock()),
    ):
        yield state


def _restart(state) -> None:
    state.down = {_DENSE: _DOWN, _SPARSE: _DOWN}
    state.health_down = {_DENSE, _SPARSE}


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
    """Registers the tasks; each keeps its retry strategy and a recorded re-queue."""

    def __init__(self) -> None:
        self.tasks: dict = {}
        self.retry: dict = {}

    def task(self, **kwargs):
        def _decorator(fn):
            fn.configure = MagicMock(return_value=SimpleNamespace(defer_async=AsyncMock()))
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


def _jobs():
    app = _App()
    enrichment_tasks._register_tasks(app)
    return app


async def _run_enrichment(
    app,
    *,
    attempts: int = 0,
    embedding_deferred_since: int | None = None,
    llm=None,
    finish: AsyncMock | None = None,
) -> SimpleNamespace:
    """Run the bulk enrichment job; return the state writer and the LLM call."""
    finish = finish or AsyncMock()

    async def _enrich(**kw):
        return [EnrichedChunk(c, f"ctx {c}", "ctx", []) for c in kw["chunks"]]

    llm = llm or AsyncMock(side_effect=_enrich)
    with (
        patch.object(enrichment_tasks, "_load_and_enrich", _load_and_enrich),
        patch.object(enrichment_tasks, "_finish", finish),
        patch.object(enrichment_tasks, "_link_graph_fields", AsyncMock()),
        patch.object(enrichment_tasks, "_still_current_for_write", AsyncMock(return_value=True)),
        patch.object(enrichment_tasks.enrichment, "enrich_chunks", llm),
        patch.object(enrichment_tasks.kb_config, "get_kb_aliases", AsyncMock(return_value=[])),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_product_rules", AsyncMock(return_value=[])
        ),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_visibility", AsyncMock(return_value="internal")
        ),
    ):
        context = SimpleNamespace(job=SimpleNamespace(attempts=attempts, queueing_lock="lock"))
        await app.tasks["enrich_document_bulk"](
            context, artifact_id=ART, embedding_deferred_since=embedding_deferred_since
        )
    return SimpleNamespace(finish=finish, llm=llm)


def _requeued(app) -> dict | None:
    task = app.tasks["enrich_document_bulk"]
    if not task.configure.called:
        return None
    return {
        **task.configure.call_args.kwargs,
        **task.configure.return_value.defer_async.call_args.kwargs,
    }


async def test_a_restart_defers_before_the_model_work_without_spending_attempts(
    mem_client, services
):
    await _write_raw()
    before = await _points(mem_client)
    _restart(services)
    app = _jobs()

    run = await _run_enrichment(app)  # returns: the job succeeds as a deferral

    run.llm.assert_not_awaited()  # the health check came first
    run.finish.assert_not_awaited()
    requeued = _requeued(app)
    assert requeued["schedule_in"] == {"seconds": EMBEDDING_RETRY_SECONDS}
    assert requeued["queueing_lock"] == "lock"
    assert abs(requeued["embedding_deferred_since"] - time.time()) < 5
    assert [p.vector for p in await _points(mem_client)] == [p.vector for p in before]


@pytest.mark.parametrize("host", [_DENSE, _SPARSE])
async def test_a_service_still_loading_its_model_counts_as_down(services, host):
    """A restarting service answers its health check with 503 before it serves."""
    services.health_status[host] = 503

    with pytest.raises(EmbeddingUnavailable):
        await embedder.check_available()


@pytest.mark.parametrize("host", [_DENSE, _SPARSE])
async def test_an_outage_that_begins_mid_job_is_deferred_and_writes_nothing(
    mem_client, services, host
):
    """Dense down during enrichment is the same condition as sparse down."""
    await _write_raw()
    before = await _points(mem_client)
    services.down[host] = _DOWN  # health still answers: the outage starts after the check
    app = _jobs()

    run = await _run_enrichment(app)

    run.llm.assert_awaited_once()
    run.finish.assert_not_awaited()
    assert _requeued(app)["embedding_deferred_since"] is not None
    after = await _points(mem_client)
    assert [p.vector for p in after] == [p.vector for p in before]
    assert all("context_prefix" not in p.payload for p in after)


async def test_an_outage_longer_than_the_window_fails_the_document(mem_client, services):
    _restart(services)
    app = _jobs()
    finish = AsyncMock()
    since = int(time.time()) - EMBEDDING_OUTAGE_WINDOW_SECONDS

    with pytest.raises(enrichment_tasks._DerivedWorkFailed) as ended:
        await _run_enrichment(app, embedding_deferred_since=since, finish=finish)

    finish.assert_awaited_once_with("enrichment", ART, "failed", "embedding_unavailable")
    retry = app.retry["enrich_document_bulk"]
    assert retry.get_retry_decision(exception=ended.value, job=SimpleNamespace(attempts=0)) is None
    assert _requeued(app) is None


async def test_back_within_the_window_writes_both_vectors(mem_client, services):
    await _write_raw()
    app = _jobs()

    run = await _run_enrichment(app, embedding_deferred_since=int(time.time()) - 300)

    run.finish.assert_awaited_once_with("enrichment", ART, "done")
    points = await _points(mem_client)
    assert [p.payload["context_prefix"] for p in points] == ["ctx", "ctx"]
    assert [p.vector["vector_sparse"].indices for p in points] == [
        [len("ctx alpha text")],
        [len("ctx beta")],
    ]


async def test_an_unrelated_error_after_the_ride_out_keeps_its_normal_attempts(
    mem_client, services
):
    """The re-queued job is a fresh job: its first ordinary failure is retried
    and does not fail the document."""
    app = _jobs()
    llm = AsyncMock(side_effect=RuntimeError("unrelated"))

    with pytest.raises(RuntimeError) as raised:
        await _run_enrichment(app, embedding_deferred_since=int(time.time()) - 600, llm=llm)

    retry = app.retry["enrich_document_bulk"]
    assert retry.get_retry_decision(exception=raised.value, job=SimpleNamespace(attempts=0))


@pytest.mark.parametrize("status", [422, 413])
async def test_a_request_the_sidecar_rejects_fails_the_document_at_once(
    mem_client, services, status
):
    services.sparse_status = status
    app = _jobs()
    finish = AsyncMock()

    with pytest.raises(enrichment_tasks._DerivedWorkFailed):
        await _run_enrichment(app, finish=finish)

    finish.assert_awaited_once_with("enrichment", ART, "failed", "sparse_rejected")
    assert _requeued(app) is None
    assert services.sparse_calls == 1


async def test_a_short_sidecar_blip_is_ridden_out_inside_one_call(services):
    services.down[_SPARSE] = 1

    vectors = await sparse_embedder.embed_sparse_batch(["abc"])

    assert [v.indices for v in vectors] == [[3]]
    assert services.sparse_calls == 2


async def test_base_ingest_with_the_sidecar_down_creates_no_version(services):
    """The base ingest embeds sparse before it records a version, like dense, so
    an outage fails the request with nothing written and a retry ingests again."""
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document
    from tests.test_enrichment_dedup import _base_patches, _make_mock_app, _make_mock_conn

    services.down[_SPARSE] = _DOWN
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
            pytest.raises(EmbeddingUnavailable),
        ):
            await ingest_document(_make_mock_conn(), req)

    create.assert_not_awaited()
    up.assert_not_awaited()


async def test_storage_refuses_a_point_without_its_sparse_vector(mem_client, services):
    """The one function that writes points checks it, so no caller can skip it."""
    await _write_raw()
    before = await _points(mem_client)
    dense_only = PointStruct(id=str(uuid.uuid4()), vector={"vector_chunk": [1.0, 0, 0, 0]})

    with pytest.raises(ValueError, match="without a sparse vector"):
        await qs._replace_points(
            mem_client, [FieldCondition(key="path", match=MatchValue(value=PATH))], [dense_only]
        )

    assert [p.id for p in await _points(mem_client)] == [p.id for p in before]
