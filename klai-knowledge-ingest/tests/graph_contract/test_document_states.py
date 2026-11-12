"""Every path of the enrichment and graph jobs leaves the document saying what happened.

Runs the real job functions against a real Postgres (the artifact rows the state
is stored on) and, for the graph, a real FalkorDB; only LLM work and the vector
write are replaced. Each path ends as one of: done, not applicable with a
reason, failed with a reason (and the job ends failed), or abandoned because the
version is no longer current (no state at all).
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import structlog

from knowledge_ingest import enrichment_tasks, llm_capacity
from knowledge_ingest import graph as graph_module
from knowledge_ingest.derived_state import ProductState, product_state, set_product_state
from tests.graph_contract.conftest import Contract, seed_graph


class _App:
    """Registers the tasks and keeps their retry strategy."""

    def __init__(self) -> None:
        self.tasks: dict = {}
        self.retry: dict = {}

    def task(self, **kwargs):
        def _decorator(fn):
            self.tasks[fn.__name__] = fn
            self.retry[fn.__name__] = kwargs.get("retry")
            return fn

        return _decorator


@pytest.fixture
def jobs(graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch, real_procrastinate):
    conn, insert = tenant_rows

    @asynccontextmanager
    async def _conn(*_args):
        yield conn

    monkeypatch.setattr(enrichment_tasks, "tenant_scoped_connection", _conn)
    monkeypatch.setattr(enrichment_tasks, "cross_org_admin_connection", _conn)
    app = _App()
    enrichment_tasks._register_tasks(app)
    return SimpleNamespace(conn=conn, insert=insert, app=app, procrastinate=real_procrastinate)


async def _state(conn, artifact_id: str, product: str) -> ProductState:
    row = await conn.fetchrow(
        "SELECT extra::text AS extra, index_status FROM knowledge.artifacts WHERE id = $1",
        artifact_id,
    )
    return product_state(json.loads(row["extra"]), product, index_status=row["index_status"])


def _context(attempts: int) -> SimpleNamespace:
    return SimpleNamespace(job=SimpleNamespace(attempts=attempts, queueing_lock="lock"))


def _gives_up(jobs, task: str, exc: BaseException) -> bool:
    return jobs.app.retry[task].get_retry_decision(exception=exc, job=_context(0).job) is None


# --- graph ------------------------------------------------------------------


async def _graph(contract: Contract, jobs, monkeypatch, artifact_id: str, parts, *, attempts=0):
    async def _extract(**_kwargs):
        episode_id = parts.pop(0)
        if episode_id:
            await seed_graph(contract, [episode_id], {f"{episode_id}-fact": [episode_id]})
        return episode_id

    monkeypatch.setattr(enrichment_tasks, "split_episode_text", lambda _t: ["one"])
    monkeypatch.setattr(graph_module, "ingest_episode", _extract)
    monkeypatch.setattr(graph_module, "flush_entity_graph_data", AsyncMock())
    await jobs.app.tasks["ingest_graphiti_episode"](
        _context(attempts),
        artifact_id=artifact_id,
        org_id=contract.group_id,
        content_type="kb_article",
        belief_time_start=1,
        replace_stale=True,
    )


async def test_a_written_graph_extraction_is_done(graph_helpers, jobs, monkeypatch) -> None:
    artifact = await jobs.insert({"document_text": "Text."}, active=True)

    await _graph(graph_helpers, jobs, monkeypatch, artifact, ["ep-1"])

    assert await _state(jobs.conn, artifact, "graph") == ProductState("done")


async def test_a_graph_job_without_stored_text_fails_the_document_and_ends_failed(
    graph_helpers, jobs, monkeypatch
) -> None:
    artifact = await jobs.insert({}, active=True)

    with pytest.raises(enrichment_tasks._DerivedWorkFailed) as ended:
        await _graph(graph_helpers, jobs, monkeypatch, artifact, [])

    assert await _state(jobs.conn, artifact, "graph") == ProductState("failed", "no_stored_text")
    assert _gives_up(jobs, "ingest_graphiti_episode", ended.value)


async def test_a_graph_failure_is_retried_and_only_the_last_attempt_fails_the_document(
    graph_helpers, jobs, monkeypatch
) -> None:
    artifact = await jobs.insert({"document_text": "Text."}, active=True)
    await set_product_state(jobs.conn, artifact, "graph", "pending")

    with pytest.raises(RuntimeError) as early:
        await _graph(graph_helpers, jobs, monkeypatch, artifact, [None], attempts=0)
    assert await _state(jobs.conn, artifact, "graph") == ProductState("pending")
    assert not _gives_up(jobs, "ingest_graphiti_episode", early.value)

    with pytest.raises(RuntimeError):
        await _graph(
            graph_helpers,
            jobs,
            monkeypatch,
            artifact,
            [None],
            attempts=enrichment_tasks._GRAPHITI_MAX_ATTEMPTS,
        )
    assert await _state(jobs.conn, artifact, "graph") == ProductState("failed", "retries_exhausted")


async def test_a_quarantined_graph_job_fails_the_document(graph_helpers, jobs, monkeypatch) -> None:
    from knowledge_ingest.falkordb_probe import FalkorDBDiedDuringEpisode

    artifact = await jobs.insert({"document_text": "Text."}, active=True)
    monkeypatch.setattr(enrichment_tasks, "split_episode_text", lambda _t: ["one"])
    monkeypatch.setattr(
        graph_module, "ingest_episode", AsyncMock(side_effect=FalkorDBDiedDuringEpisode("reset"))
    )
    deaths = enrichment_tasks._FALKORDB_DEATHS_BEFORE_QUARANTINE
    for attempt in range(deaths):
        last = attempt == deaths - 1
        expected = (
            enrichment_tasks.GraphitiEpisodeQuarantined if last else FalkorDBDiedDuringEpisode
        )
        with pytest.raises(expected) as ended:
            await jobs.app.tasks["ingest_graphiti_episode"](
                _context(attempt),
                artifact_id=artifact,
                org_id=graph_helpers.group_id,
                content_type="kb_article",
                belief_time_start=1,
            )

    assert await _state(jobs.conn, artifact, "graph") == ProductState("failed", "quarantined")
    assert _gives_up(jobs, "ingest_graphiti_episode", ended.value)


async def test_a_job_for_a_replaced_version_sets_no_state(graph_helpers, jobs, monkeypatch) -> None:
    artifact = await jobs.insert({"document_text": "Text."}, active=False)

    with pytest.raises(jobs.procrastinate.exceptions.JobAborted):
        await _graph(graph_helpers, jobs, monkeypatch, artifact, ["ep-1"])

    assert await _state(jobs.conn, artifact, "graph") == ProductState("unknown")


async def test_a_job_that_gives_up_ends_failed_in_the_real_worker(
    graph_helpers, jobs, monkeypatch
) -> None:
    """procrastinate's own worker and retry strategy: failed after one run, no retry."""
    from procrastinate.testing import InMemoryConnector

    artifact = await jobs.insert({}, active=True)
    connector = InMemoryConnector()
    app = jobs.procrastinate.App(connector=connector)
    enrichment_tasks._register_tasks(app)
    async with app.open_async():
        await app.ingest_graphiti_episode.defer_async(
            artifact_id=artifact,
            org_id=graph_helpers.group_id,
            content_type="kb_article",
            belief_time_start=1,
        )
        await app.run_worker_async(
            queues=["graphiti-bulk"], wait=False, install_signal_handlers=False
        )

    assert [(j["status"], j["attempts"]) for j in connector.jobs.values()] == [("failed", 1)]
    assert await _state(jobs.conn, artifact, "graph") == ProductState("failed", "no_stored_text")


# --- enrichment -------------------------------------------------------------


async def _enrich(jobs, artifact_id: str, *, attempts=0, error=None, **args):
    async def _chunks(**kw):
        if error:
            raise error
        return [enrichment_tasks.enrichment.EnrichedChunk(c, c, "", []) for c in kw["chunks"]]

    with (
        patch.object(
            enrichment_tasks.qdrant_store, "has_chunks_from", AsyncMock(return_value=False)
        ),
        patch.object(enrichment_tasks.qdrant_store, "upsert_enriched_chunks", AsyncMock()),
        patch.object(enrichment_tasks.enrichment, "enrich_chunks", _chunks),
        patch.object(
            enrichment_tasks.embedder, "embed", AsyncMock(side_effect=lambda t: [[0.1]] * len(t))
        ),
        patch.object(
            enrichment_tasks.sparse_embedder,
            "embed_sparse_batch",
            AsyncMock(side_effect=lambda t: [None] * len(t)),
        ),
        patch.object(
            enrichment_tasks.kb_config, "get_kb_visibility", AsyncMock(return_value="internal")
        ),
        patch.object(enrichment_tasks.kb_config, "get_kb_aliases", AsyncMock(return_value=[])),
    ):
        await jobs.app.tasks["enrich_document_bulk"](
            _context(attempts), artifact_id=artifact_id, **args
        )


async def test_written_enrichment_is_done_and_a_reindex_leaves_pending(jobs) -> None:
    artifact = await jobs.insert({"document_text": "Some text to enrich."}, active=True)
    await jobs.conn.execute(
        "UPDATE knowledge.artifacts SET index_status = 'pending' WHERE id = $1", artifact
    )

    await _enrich(jobs, artifact)

    assert await _state(jobs.conn, artifact, "enrichment") == ProductState("done")
    assert (
        await jobs.conn.fetchval(
            "SELECT index_status FROM knowledge.artifacts WHERE id = $1", artifact
        )
        == "synced"
    )


async def test_an_infrastructure_failure_fails_the_document_on_the_last_attempt_only(jobs) -> None:
    artifact = await jobs.insert(
        {"document_text": "Some text to enrich.", "source_connector_id": None}, active=True
    )
    await set_product_state(jobs.conn, artifact, "enrichment", "pending")
    outage = ConnectionError("embedding service unreachable")

    with pytest.raises(ConnectionError):
        await _enrich(jobs, artifact, error=outage)
    assert await _state(jobs.conn, artifact, "enrichment") == ProductState("pending")

    with structlog.testing.capture_logs() as logs, pytest.raises(ConnectionError):
        await _enrich(jobs, artifact, attempts=enrichment_tasks._BULK_MAX_ATTEMPTS, error=outage)
    assert await _state(jobs.conn, artifact, "enrichment") == ProductState(
        "failed", "retries_exhausted"
    )
    failed = [e for e in logs if e["event"] == "derived_product_failed"]
    assert [(e["log_level"], e["product"], e["reason"], e["artifact_id"]) for e in failed] == [
        ("error", "enrichment", "retries_exhausted", artifact)
    ]


async def test_spent_llm_capacity_fails_the_document_for_any_source(jobs, monkeypatch) -> None:
    artifact = await jobs.insert(
        {"document_text": "Some text to enrich.", "source_connector_id": str(uuid.uuid4())},
        active=True,
    )
    monkeypatch.setattr(enrichment_tasks, "is_llm_capacity_error", lambda _exc: True)
    monkeypatch.setattr(
        enrichment_tasks,
        "_abandon_if_connector_inactive",
        AsyncMock(),  # the connector is active; its state is not under test here
    )
    monkeypatch.setattr(enrichment_tasks, "_still_current_for_write", AsyncMock(return_value=True))
    spent = 1  # deferred since the epoch: the window is long gone
    assert llm_capacity.deferral_window_spent(spent)

    with pytest.raises(enrichment_tasks._DerivedWorkFailed) as ended:
        await _enrich(
            jobs,
            artifact,
            error=enrichment_tasks.enrichment.EnrichmentError("key budget spent"),
            llm_deferred_since=spent,
        )

    assert await _state(jobs.conn, artifact, "enrichment") == ProductState(
        "failed", "llm_capacity_exhausted"
    )
    assert _gives_up(jobs, "enrich_document_bulk", ended.value)


async def test_enrichment_without_stored_text_fails_the_document(jobs) -> None:
    artifact = await jobs.insert({"title": "Legacy row"}, active=True)

    with pytest.raises(enrichment_tasks._DerivedWorkFailed):
        await _enrich(jobs, artifact)

    assert await _state(jobs.conn, artifact, "enrichment") == ProductState(
        "failed", "no_stored_text"
    )


async def test_a_document_too_large_to_enrich_is_not_applicable(jobs, monkeypatch) -> None:
    monkeypatch.setattr(enrichment_tasks, "enrichment_skip_reason", lambda **_kw: "too_many_chunks")
    artifact = await jobs.insert({"document_text": "Text."}, active=True)

    await _enrich(jobs, artifact)

    assert await _state(jobs.conn, artifact, "enrichment") == ProductState(
        "not_applicable", "too_many_chunks"
    )


async def test_a_queue_outage_leaves_the_document_failed_and_raises(jobs) -> None:
    from knowledge_ingest.derived_state import queue_derived_work

    artifact = await jobs.insert({"document_text": "Text."}, active=True)
    task = SimpleNamespace(defer_async=AsyncMock(side_effect=ConnectionError("queue down")))

    with structlog.testing.capture_logs() as logs, pytest.raises(ConnectionError):
        await queue_derived_work(jobs.conn, artifact, "graph", task, artifact_id=artifact)

    assert await _state(jobs.conn, artifact, "graph") == ProductState("failed", "queue_unavailable")
    assert [e["reason"] for e in logs if e["event"] == "derived_product_failed"] == [
        "queue_unavailable"
    ]


async def test_a_repeated_failure_alerts_once(jobs) -> None:
    artifact = await jobs.insert({}, active=True)

    with structlog.testing.capture_logs() as logs:
        for _ in range(2):
            await set_product_state(jobs.conn, artifact, "graph", "failed", "quarantined")

    assert len([e for e in logs if e["event"] == "derived_product_failed"]) == 1


async def test_a_replaced_version_never_records_a_failure(jobs) -> None:
    """Work stopped because the document moved on is not a failure to alert on."""
    artifact = await jobs.insert({}, active=False)

    with structlog.testing.capture_logs() as logs:
        written = await set_product_state(jobs.conn, artifact, "graph", "failed", "quarantined")

    assert written is False
    assert await _state(jobs.conn, artifact, "graph") == ProductState("unknown")
    assert not [e for e in logs if e["event"] == "derived_product_failed"]
