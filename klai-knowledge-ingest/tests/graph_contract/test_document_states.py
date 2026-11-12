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

from knowledge_ingest import contextual, enrichment_tasks, llm_capacity
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
        patch.object(contextual, "generate_document_summary", AsyncMock(return_value="S.")),
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


# --- a cancelled job is retried, whatever its attempt count ------------------


@pytest.mark.parametrize("how", ["worker_shutdown", "user_abort"])
async def test_a_job_cancelled_on_its_last_attempt_runs_again(jobs, monkeypatch, how) -> None:
    """A cancellation is not a failure: on the last attempt it would otherwise
    end the job aborted with no retry left and the document pending for ever."""
    import asyncio

    from procrastinate.testing import InMemoryConnector

    artifact = await jobs.insert({"document_text": "Text."}, active=True)
    await set_product_state(jobs.conn, artifact, "enrichment", "pending")
    started = asyncio.Event()

    async def _hang(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(enrichment_tasks, "_load_and_enrich", _hang)
    connector = InMemoryConnector()
    app = jobs.procrastinate.App(connector=connector)
    enrichment_tasks._register_tasks(app)
    async with app.open_async():
        job_id = await app.enrich_document_bulk.defer_async(artifact_id=artifact)
        connector.jobs[job_id]["attempts"] = enrichment_tasks._BULK_MAX_ATTEMPTS
        worker = asyncio.create_task(
            app.run_worker_async(
                queues=["enrich-bulk"],
                install_signal_handlers=False,
                shutdown_graceful_timeout=0,
                abort_job_polling_interval=0.05,
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        if how == "user_abort":
            await app.job_manager.cancel_job_by_id_async(job_id, abort=True)
            for _ in range(100):
                if connector.jobs[job_id]["status"] != "doing":
                    break
                await asyncio.sleep(0.05)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    assert connector.jobs[job_id]["status"] == "todo"
    assert await _state(jobs.conn, artifact, "enrichment") == ProductState("pending")


# --- the one writer: legal transitions, one atomic step ----------------------


async def test_a_late_write_cannot_turn_failed_into_done_or_done_into_failed(jobs) -> None:
    failed = await jobs.insert({}, active=True, path="failed")
    done = await jobs.insert({}, active=True, path="done")
    for artifact, outcome in ((failed, ("failed", "quarantined")), (done, ("done", None))):
        await set_product_state(jobs.conn, artifact, "graph", "pending")
        await set_product_state(jobs.conn, artifact, "graph", *outcome)

    with structlog.testing.capture_logs() as logs:
        assert not await set_product_state(jobs.conn, failed, "graph", "done")
        assert not await set_product_state(jobs.conn, done, "graph", "failed", "retries_exhausted")

    assert await _state(jobs.conn, failed, "graph") == ProductState("failed", "quarantined")
    assert await _state(jobs.conn, done, "graph") == ProductState("done")
    assert [e["event"] for e in logs] == ["derived_state_transition_refused"] * 2
    # Re-queuing is the deliberate way out of failed.
    assert await set_product_state(jobs.conn, failed, "graph", "pending")


async def _write_behind_a_lock(artifact: str, change: str, writes: int) -> list[bool]:
    """Hold the row, start ``writes`` concurrent failures, apply ``change``, release."""
    import asyncio

    import asyncpg

    from tests.graph_contract.conftest import _PG_DSN

    holder = await asyncpg.connect(_PG_DSN)
    writers = [await asyncpg.connect(_PG_DSN) for _ in range(writes)]
    try:
        tx = holder.transaction()
        await tx.start()
        await holder.execute("SELECT 1 FROM knowledge.artifacts WHERE id = $1 FOR UPDATE", artifact)
        pending = [
            asyncio.create_task(set_product_state(w, artifact, "graph", "failed", "quarantined"))
            for w in writers
        ]
        await asyncio.sleep(0.3)  # every writer is now waiting on the row lock
        await holder.execute(change, artifact)
        await tx.commit()
        return list(await asyncio.gather(*pending))
    finally:
        for c in (holder, *writers):
            await c.close()


async def test_two_concurrent_failures_alert_once(jobs) -> None:
    artifact = await jobs.insert({}, active=True)

    with structlog.testing.capture_logs() as logs:
        await _write_behind_a_lock(artifact, "SELECT $1::uuid", writes=2)

    assert len([e for e in logs if e["event"] == "derived_product_failed"]) == 1


async def test_a_replacement_committing_first_keeps_the_write_off_the_old_version(jobs) -> None:
    artifact = await jobs.insert({}, active=True)

    with structlog.testing.capture_logs() as logs:
        written = await _write_behind_a_lock(
            artifact, "UPDATE knowledge.artifacts SET belief_time_end = 2 WHERE id = $1", 1
        )

    assert written == [False]
    assert await _state(jobs.conn, artifact, "graph") == ProductState("unknown")
    assert not [e for e in logs if e["event"] == "derived_product_failed"]


# --- graph refresh does not re-queue what cannot succeed ---------------------


@pytest.mark.parametrize(
    ("reason", "build", "expected"),
    [
        ("no_stored_text", "older", "held:no_stored_text"),
        ("quarantined", "current", "held:quarantined"),
        ("quarantined", "older", "queued"),
    ],
)
async def test_an_unchanged_resync_does_not_requeue_a_failure_that_would_repeat(
    graph_helpers, jobs, monkeypatch, reason, build, expected
) -> None:
    from knowledge_ingest import graph_refresh
    from knowledge_ingest.falkordb_probe import graph_writer_build

    extra = {"graphiti_quarantine_build": graph_writer_build() if build == "current" else "old"}
    artifact = await jobs.insert(extra, active=True)
    await set_product_state(jobs.conn, artifact, "graph", "pending")
    await set_product_state(jobs.conn, artifact, "graph", "failed", reason)
    stored = json.loads(
        await jobs.conn.fetchval(
            "SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact
        )
    )
    task = SimpleNamespace(defer_async=AsyncMock())
    monkeypatch.setattr(
        enrichment_tasks,
        "get_app",
        lambda: SimpleNamespace(
            ingest_graphiti_episode=SimpleNamespace(configure=lambda **_kw: task)
        ),
    )
    monkeypatch.setattr(
        graph_refresh.pg_store, "reserve_graph_refresh_slot", AsyncMock(return_value=True)
    )

    with structlog.testing.capture_logs() as logs:
        result = await graph_refresh.maybe_refresh_stale_graph(
            jobs.conn,
            artifact_id=artifact,
            extra=stored,
            org_id=graph_helpers.group_id,
            kb_slug="docs",
            path="page",
            content_type="kb_article",
            belief_time_start=1,
            indexable_content="A sentence about the product. Another sentence about it.",
        )

    assert result == expected
    assert task.defer_async.await_count == (expected == "queued")
    held = expected.startswith("held")
    assert await _state(jobs.conn, artifact, "graph") == (
        ProductState("failed", reason) if held else ProductState("pending")
    )
    assert not [e for e in logs if e["event"] == "derived_product_failed"]


# --- what the sources list and the operator tools read -----------------------


async def test_a_reindex_whose_job_cannot_be_queued_shows_the_upload_failed(jobs) -> None:
    from knowledge_ingest import pg_store
    from knowledge_ingest.derived_state import queue_derived_work

    artifact = await jobs.insert({"document_text": "Text."}, active=True)
    # What reindex_upload sets before it queues.
    await jobs.conn.execute(
        "UPDATE knowledge.artifacts SET index_status = 'pending' WHERE id = $1", artifact
    )
    task = SimpleNamespace(defer_async=AsyncMock(side_effect=ConnectionError("queue down")))

    with pytest.raises(ConnectionError):
        await queue_derived_work(jobs.conn, artifact, "enrichment", task, artifact_id=artifact)

    org_id = await jobs.conn.fetchval("SELECT current_setting('app.current_org_id')")
    (upload,) = (await pg_store.list_kb_sources(jobs.conn, org_id, "docs"))["uploads"]
    assert upload["index_status"] == "failed"


async def test_pages_failed_counts_text_and_enrichment_failures_through_the_reader(jobs) -> None:
    from knowledge_ingest import pg_store

    async def _version(ref: str, index_status: str = "synced", **extra) -> str:
        artifact = await jobs.insert(extra, active=True, path=ref, source_ref=ref)
        await jobs.conn.execute(
            "UPDATE knowledge.artifacts SET index_status = $2 WHERE id = $1", artifact, index_status
        )
        return artifact

    await _version("raw-write-failed", "failed")
    await _version("enrichment-failed", enrichment_state="failed", enrichment_reason="x")
    await _version("graph-failed", graph_state="failed", graph_reason="quarantined")
    await _version("legacy-quarantine", graphiti_quarantined_at=1, graphiti_episode_ids=[])
    await _version("done", enrichment_state="done", graph_state="done")
    org_id = await jobs.conn.fetchval("SELECT current_setting('app.current_org_id')")

    (connector,) = (await pg_store.list_kb_sources(jobs.conn, org_id, "docs"))["connectors"]

    assert (connector["items_count"], connector["items_failed_count"]) == (5, 2)


async def test_the_requeue_script_selects_what_the_reader_calls_failed(jobs) -> None:
    from knowledge_ingest.scripts import requeue_failed_artifacts as script

    async def _version(index_status: str, **extra) -> str:
        artifact = await jobs.insert(extra, active=True, path=str(uuid.uuid4()))
        await jobs.conn.execute(
            "UPDATE knowledge.artifacts SET index_status = $2 WHERE id = $1", artifact, index_status
        )
        return artifact

    enrichment_failed = await _version("synced", enrichment_state="failed")
    await _version("failed", enrichment_state="done")  # a failed raw write
    legacy_upload = await _version("failed")
    await _version("failed", source_connector_id="connector-1")  # legacy: no trace
    org_id = await jobs.conn.fetchval("SELECT current_setting('app.current_org_id')")

    selected = await script._select_failed_artifacts(
        jobs.conn, org_id=org_id, kb_slug="docs", limit=10
    )

    assert {row["artifact_id"] for row in selected} == {enrichment_failed, legacy_upload}


async def test_a_rebuild_that_fails_after_deleting_the_parents_says_failed(
    jobs, monkeypatch
) -> None:
    import asyncio

    from knowledge_ingest import rebuild_tasks

    artifact = await jobs.insert({"document_text": "# Title\n\nSome text."}, active=True)
    await set_product_state(jobs.conn, artifact, "enrichment", "pending")
    await set_product_state(jobs.conn, artifact, "enrichment", "done")

    @asynccontextmanager
    async def _conn(*_args):
        yield jobs.conn

    monkeypatch.setattr(rebuild_tasks, "tenant_scoped_connection", _conn)
    monkeypatch.setattr(
        enrichment_tasks, "_enrich_document", AsyncMock(side_effect=ConnectionError("down"))
    )
    row = dict(
        await jobs.conn.fetchrow("SELECT * FROM knowledge.artifacts WHERE id = $1", artifact)
    )

    result = await rebuild_tasks._rebuild_artifact(
        org_id=row["org_id"], kb_slug="docs", artifact=row, semaphore=asyncio.Semaphore(1)
    )

    assert result == "failed"
    assert await _state(jobs.conn, artifact, "enrichment") == ProductState(
        "failed", "rebuild_failed"
    )
