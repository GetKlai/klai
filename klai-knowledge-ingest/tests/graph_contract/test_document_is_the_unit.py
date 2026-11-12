"""Derived work belongs to the document version, not to the sync run that queued it.

On production about 1 in 15 bulk jobs ended cancelled or fenced out because a
newer sync run of the same connector had started (353 enrichment and 387 graph
jobs of ~11,300), while that newer run skipped the unchanged page and queued
nothing again. These tests run the real graph and enrichment jobs against a
real Postgres (artifact rows, the run fence, the connector state) and a real
FalkorDB; only the LLM work is replaced.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest import connector_state, enrichment_tasks
from knowledge_ingest import graph as graph_module
from knowledge_ingest.resource_jobs import connector_resource_key
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph
from tests.test_graph_refresh_on_stale_extraction_version import _CapturingApp

# procrastinate's own JobAborted (``real_procrastinate``), so an abandoned job is
# asserted to be what the worker records as ``aborted`` and does not retry.
JobAborted: type[BaseException]


@pytest.fixture(autouse=True)
def _job_aborted(real_procrastinate):
    global JobAborted
    JobAborted = real_procrastinate.exceptions.JobAborted


@pytest.fixture
def stores(graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch):
    conn, insert = tenant_rows
    org_id = graph_helpers.group_id
    connector_id = str(uuid.uuid4())

    @asynccontextmanager
    async def _tenant(_org_id: str):
        yield conn

    class _Pool:
        def acquire(self):
            return _tenant(org_id)

    async def _pool():
        return _Pool()

    monkeypatch.setattr(enrichment_tasks, "tenant_scoped_connection", _tenant)
    monkeypatch.setattr(enrichment_tasks, "cross_org_admin_connection", lambda: _tenant(org_id))
    monkeypatch.setattr(connector_state, "tenant_scoped_connection", _tenant)
    monkeypatch.setattr(connector_state, "get_pool", _pool)
    connector_state.invalidate_cache()

    async def setup() -> None:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS public.portal_connectors (id uuid PRIMARY KEY, state text)"
        )
        await conn.execute(
            "INSERT INTO public.portal_connectors (id, state) VALUES ($1, 'active')", connector_id
        )

    return SimpleNamespace(
        conn=conn, insert=insert, org_id=org_id, connector_id=connector_id, setup=setup
    )


async def _set_connector(stores, state: str | None) -> None:
    if state is None:
        await stores.conn.execute(
            "DELETE FROM public.portal_connectors WHERE id = $1", stores.connector_id
        )
    else:
        await stores.conn.execute(
            "UPDATE public.portal_connectors SET state = $2 WHERE id = $1",
            stores.connector_id,
            state,
        )
    connector_state.invalidate_cache()


async def _start_run(stores, generation: str) -> str:
    """What a sync or crawl run does when it starts: it becomes the connector's current run."""
    key = connector_resource_key(stores.org_id, "docs", stores.connector_id, generation)
    await connector_state.activate_connector_resource(stores.conn, key)
    return key


async def _connector_page(stores, key_of_run: str) -> str:
    return await stores.insert(
        {
            "document_text": "Two parts of text.",
            "source_connector_id": stores.connector_id,
            # Rows ingested before this change record the run that ingested them.
            "resource_key": key_of_run,
        },
        active=True,
    )


async def _graph_job(
    contract: Contract, monkeypatch, artifact_id: str, *, between_parts=None, **args
):
    parts = ["part-1", "part-2"]

    async def _extract(**_kwargs):
        episode_id = parts.pop(0)
        await seed_graph(contract, [episode_id], {f"{episode_id}-fact": [episode_id]})
        if between_parts and episode_id == "part-1":
            await between_parts()
        return episode_id

    monkeypatch.setattr(enrichment_tasks, "split_episode_text", lambda _text: ["one", "two"])
    monkeypatch.setattr(graph_module, "ingest_episode", _extract)
    monkeypatch.setattr(graph_module, "flush_entity_graph_data", AsyncMock())
    app = _CapturingApp()
    enrichment_tasks._register_tasks(app)
    await app.tasks["ingest_graphiti_episode"](
        artifact_id=artifact_id,
        org_id=contract.group_id,
        content_type="kb_article",
        belief_time_start=1,
        kb_slug="docs",
        path="page",
        replace_stale=True,
        **args,
    )


@pytest.mark.parametrize("queued_with_run_key", [True, False], ids=["in-flight-shape", "new-shape"])
async def test_graph_work_of_run_a_still_completes_after_run_b_starts(
    graph_helpers: Contract, stores, monkeypatch, queued_with_run_key: bool
) -> None:
    """The reported case: run B finds the page unchanged and queues nothing."""
    await stores.setup()
    run_a = await _start_run(stores, "run-a")
    artifact = await _connector_page(stores, run_a)
    await _start_run(stores, "run-b")

    await _graph_job(
        graph_helpers,
        monkeypatch,
        artifact,
        **({"resource_key": run_a} if queued_with_run_key else {}),
    )

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert episodes == {"part-1", "part-2"}
    assert set(edges) == {"part-1-fact", "part-2-fact"}


async def _enrichment_job(*, upsert=None, during_model_work=None, **args) -> AsyncMock:
    """Run the real bulk enrichment job; only the model work and the vector write are faked."""
    upsert = upsert or AsyncMock()

    async def _enrich(**kw):
        if during_model_work:
            await during_model_work()
        return [enrichment_tasks.enrichment.EnrichedChunk(c, c, "", []) for c in kw["chunks"]]

    with (
        patch.object(
            enrichment_tasks.qdrant_store, "has_chunks_from", AsyncMock(return_value=False)
        ),
        patch.object(enrichment_tasks.qdrant_store, "upsert_enriched_chunks", upsert),
        patch.object(enrichment_tasks.enrichment, "enrich_chunks", _enrich),
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
        app = _CapturingApp()
        enrichment_tasks._register_tasks(app)
        await app.tasks["enrich_document_bulk"](None, **args)
    return upsert


async def test_enrichment_of_run_a_still_writes_after_run_b_starts(stores) -> None:
    await stores.setup()
    run_a = await _start_run(stores, "run-a")
    artifact = await _connector_page(stores, run_a)
    await _start_run(stores, "run-b")

    # Queued by run A before this change: the job still carries its run key.
    upsert = await _enrichment_job(artifact_id=artifact, resource_key=run_a)

    upsert.assert_awaited_once()
    assert upsert.await_args.kwargs["artifact_id"] == artifact


async def _flip_connector_without_touching_the_cache(stores, state: str) -> None:
    """Production order: the job has already cached "active" when the state flips."""
    await stores.conn.execute(
        "UPDATE public.portal_connectors SET state = $2 WHERE id = $1", stores.connector_id, state
    )


async def test_enrichment_does_not_write_after_its_connector_started_deleting(stores) -> None:
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))

    with pytest.raises(JobAborted):
        upsert = AsyncMock()
        await _enrichment_job(
            artifact_id=artifact,
            upsert=upsert,
            during_model_work=lambda: _flip_connector_without_touching_the_cache(
                stores, "deleting"
            ),
        )
    upsert.assert_not_awaited()


async def test_a_failed_check_before_the_vector_write_fails_the_job(stores, monkeypatch) -> None:
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))
    # The job-start read succeeds; the check right before the write cannot be made.
    monkeypatch.setattr(
        enrichment_tasks.pg_store,
        "artifact_is_active",
        AsyncMock(side_effect=ConnectionError("database unavailable")),
    )
    upsert = AsyncMock()

    with pytest.raises(enrichment_tasks._WriteCheckFailed):
        await _enrichment_job(artifact_id=artifact, upsert=upsert)
    upsert.assert_not_awaited()


async def test_a_failed_check_after_the_vector_write_fails_and_a_retry_removes_the_points(
    stores, monkeypatch
) -> None:
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))
    real_check = enrichment_tasks.pg_store.artifact_is_active
    calls = []

    async def _check(conn, artifact_id):
        calls.append(artifact_id)
        if len(calls) > 1:  # the check after the write
            raise ConnectionError("database unavailable")
        return await real_check(conn, artifact_id)

    async def _upsert_while_a_delete_lands(**_kwargs):
        await stores.conn.execute("DELETE FROM knowledge.artifacts WHERE id = $1", artifact)

    monkeypatch.setattr(enrichment_tasks.pg_store, "artifact_is_active", _check)

    with pytest.raises(enrichment_tasks._WriteCheckFailed):
        await _enrichment_job(
            artifact_id=artifact, upsert=AsyncMock(side_effect=_upsert_while_a_delete_lands)
        )

    # The retry finds the version gone and removes the points the first attempt wrote.
    point = SimpleNamespace(payload={"org_id": stores.org_id})
    vector_store = SimpleNamespace(
        scroll=AsyncMock(return_value=([point], None)), delete=AsyncMock()
    )
    monkeypatch.setattr(enrichment_tasks.qdrant_store, "get_client", lambda: vector_store)
    with pytest.raises(JobAborted):
        await _enrichment_job(artifact_id=artifact)
    deleted = vector_store.delete.await_args.kwargs["points_selector"]
    assert {
        c.key: getattr(c.match, "value", None) or getattr(c.match, "any", None)
        for c in deleted.must + deleted.should
    } == {
        "org_id": stores.org_id,
        "artifact_id": [artifact],
    }


async def test_a_graph_part_written_after_the_connector_started_deleting_is_removed(
    graph_helpers: Contract, stores, monkeypatch
) -> None:
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))

    await _assert_abandoned_with_nothing_written(
        graph_helpers,
        monkeypatch,
        artifact,
        between_parts=lambda: _flip_connector_without_touching_the_cache(stores, "deleting"),
    )


async def test_an_abandoned_job_ends_aborted_and_is_not_retried(
    graph_helpers: Contract, stores, monkeypatch, real_procrastinate
) -> None:
    """Through procrastinate's own worker: the job row says aborted, not succeeded."""
    from procrastinate.testing import InMemoryConnector

    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))
    await _set_connector(stores, "deleting")
    monkeypatch.setattr(enrichment_tasks, "split_episode_text", lambda _text: ["one"])
    connector = InMemoryConnector()
    app = real_procrastinate.App(connector=connector)
    enrichment_tasks._register_tasks(app)

    async with app.open_async():
        await app.ingest_graphiti_episode.defer_async(
            artifact_id=artifact,
            org_id=graph_helpers.group_id,
            content_type="kb_article",
            belief_time_start=1,
            kb_slug="docs",
            path="page",
            replace_stale=True,
        )
        await app.run_worker_async(
            queues=["graphiti-bulk"], wait=False, install_signal_handlers=False
        )

    jobs = list(connector.jobs.values())
    # One attempt, one row: aborted, and no retry was scheduled.
    assert [(job["status"], job["attempts"]) for job in jobs] == [("aborted", 1)]


async def _assert_abandoned_with_nothing_written(contract, monkeypatch, artifact, **kw) -> None:
    with pytest.raises(JobAborted):
        await _graph_job(contract, monkeypatch, artifact, **kw)
    episodes, edges, _ = await stored_graph(contract)
    assert episodes == set() and edges == {}


@pytest.mark.parametrize("state", ["deleting", None], ids=["connector-deleting", "connector-gone"])
async def test_a_queued_job_of_a_connector_being_deleted_is_abandoned(
    graph_helpers: Contract, stores, monkeypatch, state
) -> None:
    """Covers connector delete, and a connector deleted and added again (a new id)."""
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))
    await _set_connector(stores, state)

    await _assert_abandoned_with_nothing_written(graph_helpers, monkeypatch, artifact)


async def test_a_running_job_stops_and_removes_its_part_when_the_connector_is_deleted(
    graph_helpers: Contract, stores, monkeypatch
) -> None:
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))

    await _assert_abandoned_with_nothing_written(
        graph_helpers,
        monkeypatch,
        artifact,
        between_parts=lambda: _set_connector(stores, "deleting"),
    )


@pytest.mark.parametrize("when", ["queued", "running"])
@pytest.mark.parametrize("change", ["replaced", "deleted"])
async def test_a_job_for_a_version_that_is_no_longer_current_is_abandoned(
    graph_helpers: Contract, stores, monkeypatch, when: str, change: str
) -> None:
    """Replaced by a newer version, or hard-deleted (document_purge)."""
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))

    async def _change() -> None:
        if change == "replaced":
            await stores.conn.execute(
                "UPDATE knowledge.artifacts SET belief_time_end = 2 WHERE id = $1", artifact
            )
        else:
            await stores.conn.execute("DELETE FROM knowledge.artifacts WHERE id = $1", artifact)

    if when == "queued":
        await _change()
        await _assert_abandoned_with_nothing_written(graph_helpers, monkeypatch, artifact)
    else:
        await _assert_abandoned_with_nothing_written(
            graph_helpers, monkeypatch, artifact, between_parts=_change
        )


async def test_a_job_that_cannot_read_the_connector_state_fails_and_is_retried(
    graph_helpers: Contract, stores, monkeypatch
) -> None:
    await stores.setup()
    artifact = await _connector_page(stores, await _start_run(stores, "run-a"))

    async def _broken_pool():
        raise ConnectionError("database unavailable")

    monkeypatch.setattr(connector_state, "get_pool", _broken_pool)

    with pytest.raises(ConnectionError):
        await _graph_job(graph_helpers, monkeypatch, artifact)
    assert (await stored_graph(graph_helpers))[0] == set()
