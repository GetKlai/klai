"""Document jobs in procrastinate's real queue tables when a new run starts or a connector goes.

On production a new sync or crawl run cancelled the previous run's enrichment
and graph jobs (353 enrichment and 387 graph jobs of ~11,300) while skipping the
unchanged pages, so nothing queued that work again. These tests put real job
rows in ``procrastinate_jobs`` and check what is left after the real code ran.
Needs ``GRAPH_REFRESH_BUDGET_TEST_DSN`` and the real procrastinate (libpq).
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from knowledge_ingest import connector_state, enrichment_tasks
from knowledge_ingest.resource_jobs import cancel_jobs_by_artifact_ids, connector_resource_key
from knowledge_ingest.routes import crawl_sync

_DSN = os.environ.get("GRAPH_REFRESH_BUDGET_TEST_DSN")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="GRAPH_REFRESH_BUDGET_TEST_DSN is not configured"),
]


@pytest.fixture
async def queue(real_procrastinate):
    """A real procrastinate app on the test database, its pool, and a tenant connection."""
    from procrastinate.schema import SchemaManager

    if not real_procrastinate.has_libpq:
        message = "the psycopg connector needs libpq"
        if os.environ.get("CI"):
            pytest.fail(message)
        pytest.skip(message)
    assert _DSN is not None
    setup = await asyncpg.connect(_DSN)
    try:
        if await setup.fetchval("SELECT to_regclass('public.procrastinate_jobs')") is None:
            await setup.execute(SchemaManager.get_schema())
    finally:
        await setup.close()
    app = real_procrastinate.App(connector=real_procrastinate.PsycopgConnector(conninfo=_DSN))
    pool = await asyncpg.create_pool(_DSN, min_size=1, max_size=2)
    org_id = f"org-queue-{uuid.uuid4().hex[:12]}"
    conn = await asyncpg.connect(_DSN)
    await conn.execute("SELECT set_config('app.current_org_id', $1, false)", org_id)
    connector_state.invalidate_cache()
    async with app.open_async():
        yield app, pool, conn, org_id
    await conn.execute("DELETE FROM knowledge.connector_resource_fences WHERE org_id = $1", org_id)
    await conn.close()
    await pool.close()


async def _job(app, queue_name: str, task: str, **args) -> int:
    return await app.configure_task(task, queue=queue_name).defer_async(**args)


async def _status(pool, job_id: int) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT status::text FROM procrastinate_jobs WHERE id = $1", job_id
        )


async def _run_a_jobs(app, org_id: str, connector_id: str) -> tuple[str, dict[str, int]]:
    run_a = connector_resource_key(org_id, "docs", connector_id, "run-a")
    artifact = str(uuid.uuid4())
    jobs = {
        "enrich": await _job(
            app, "enrich-bulk", "enrich_document_bulk", artifact_id=artifact, resource_key=run_a
        ),
        "graph": await _job(
            app,
            "graphiti-bulk",
            "ingest_graphiti_episode",
            artifact_id=artifact,
            org_id=org_id,
            content_type="kb_article",
            belief_time_start=1,
            resource_key=run_a,
        ),
    }
    return run_a, jobs


async def test_a_new_crawl_run_cancels_only_the_older_crawl_job(queue) -> None:
    app, pool, conn, org_id = queue
    connector_id = str(uuid.uuid4())
    run_a, jobs = await _run_a_jobs(app, org_id, connector_id)
    jobs["crawl"] = await _job(app, "crawl-jobs", "run_crawl", job_id="crawl-a", resource_key=run_a)
    await connector_state.activate_connector_resource(conn, run_a)

    @asynccontextmanager
    async def _tenant(_org_id: str):
        yield conn

    with patch.object(crawl_sync, "tenant_scoped_connection", _tenant):
        await crawl_sync.start_crawl_run(
            app, pool, org_id, connector_resource_key(org_id, "docs", connector_id, "run-b")
        )

    assert {name: await _status(pool, job) for name, job in jobs.items()} == {
        "enrich": "todo",
        "graph": "todo",
        "crawl": "cancelled",
    }


async def test_the_first_ingest_of_a_new_connector_run_leaves_the_older_runs_jobs(queue) -> None:
    from knowledge_ingest.models import IngestRequest
    from knowledge_ingest.routes.ingest import ingest_document
    from tests.test_enrichment_dedup import _base_patches, _make_mock_app

    app, pool, conn, org_id = queue
    connector_id = str(uuid.uuid4())
    run_a, jobs = await _run_a_jobs(app, org_id, connector_id)
    await connector_state.activate_connector_resource(conn, run_a)
    req = IngestRequest(
        org_id=org_id,
        kb_slug="docs",
        path="connector/new-page.md",
        content="# New page\n\nA page run B found changed.",
        source_type="connector",
        source_connector_id=connector_id,
        resource_generation="run-b",  # what a connector deployed before this change sends
        content_type="plain_text",
    )

    async def _pool():
        return pool

    enrichment_tasks._register_tasks(app)  # run B queues its own jobs on the real queue
    async with _base_patches(_make_mock_app()[0], graphiti_enabled=True):
        with (
            patch.object(enrichment_tasks, "get_app", lambda: app),
            # A fresh id per run: the jobs it queues carry queueing locks on it.
            patch(
                "knowledge_ingest.routes.ingest.pg_store.create_artifact",
                AsyncMock(return_value=str(uuid.uuid4())),
            ),
            patch("knowledge_ingest.db.get_pool", _pool),
        ):
            await ingest_document(conn, req)
    # The run marker is crawl-only now: a connector's ingest leaves it where it was.
    assert await connector_state.get_current_connector_resource_key(conn, run_a) == run_a

    assert {name: await _status(pool, job) for name, job in jobs.items()} == {
        "enrich": "todo",
        "graph": "todo",
    }


async def test_connector_delete_cancels_every_job_of_its_documents_and_nothing_else(queue) -> None:
    app, pool, _conn, org_id = queue
    run_a = connector_resource_key(org_id, "docs", str(uuid.uuid4()), "run-a")
    page, large_doc, other_connectors_page = (str(uuid.uuid4()) for _ in range(3))
    purged = {
        "normal": await _job(app, "enrich-bulk", "enrich_document_bulk", artifact_id=page),
        "old-shape graph": await _job(
            app,
            "graphiti-bulk",
            "ingest_graphiti_episode",
            artifact_id=page,
            org_id=org_id,
            content_type="kb_article",
            belief_time_start=1,
            resource_key=run_a,
        ),
        "batch of a large document": await _job(
            app, "enrich-bulk", "enrich_document_bulk", artifact_id=large_doc, batch_offset=200
        ),
        "capacity-deferred": await app.configure_task(
            "enrich_document_bulk", queue="enrich-bulk", schedule_in={"hours": 1}
        ).defer_async(artifact_id=large_doc, llm_deferred_since=1),
    }
    kept = await _job(app, "enrich-bulk", "enrich_document_bulk", artifact_id=other_connectors_page)

    await cancel_jobs_by_artifact_ids(app, pool, [page, large_doc])

    assert {name: await _status(pool, job) for name, job in purged.items()} == dict.fromkeys(
        purged, "cancelled"
    )
    assert await _status(pool, kept) == "todo"


# -- a stuck job whose queueing lock another job holds ------------------------


async def _stuck_job_and_twin(app, pool, task: str, lock: str, stuck: dict, twin: dict) -> int:
    """A job left ``doing`` by a dead worker, and a later job holding its lock."""
    queue_name = f"zombie-{uuid.uuid4().hex[:8]}"
    stuck_id = await app.configure_task(task, queue=queue_name, queueing_lock=lock).defer_async(
        **stuck
    )
    worker_id = await app.job_manager.register_worker()
    await app.job_manager.fetch_job(queues=[queue_name], worker_id=worker_id)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE procrastinate_workers SET last_heartbeat = now() - interval '1 hour' "
            "WHERE id = $1",
            worker_id,
        )
    await app.configure_task(
        twin.pop("task", task), queue=queue_name, queueing_lock=lock
    ).defer_async(**twin)
    return stuck_id


async def _current_version(conn, org_id: str) -> str:
    artifact_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO knowledge.artifacts (
            id, org_id, provenance_type, assertion_mode, belief_time_start,
            belief_time_end, created_at, kb_slug, path, extra
        ) VALUES ($1, $2, 'observed', 'factual', 1, 253402300800, 1, 'docs', $3,
                  '{"enrichment_state": "pending"}'::jsonb)
        """,
        artifact_id,
        org_id,
        f"page-{artifact_id}",
    )
    return artifact_id


async def _enrichment_state(conn, artifact_id: str) -> tuple[str, str | None]:
    row = await conn.fetchrow(
        "SELECT extra->>'enrichment_state' AS s, extra->>'enrichment_reason' AS r "
        "FROM knowledge.artifacts WHERE id = $1",
        artifact_id,
    )
    return row["s"], row["r"]


@pytest.mark.parametrize(
    "twin",
    [
        {"batch_offset": 200},  # the next batch: deferred only after this one wrote
        {"batch_offset": 0, "llm_deferred_since": 1},  # the capacity re-queue of this job
        {"resource_key": "connector:org:docs:c:run-a"},  # an old-shape job
    ],
    ids=["next-batch", "capacity-requeue", "old-shape"],
)
async def test_a_stuck_job_covered_by_its_lock_holder_ends_aborted_and_alerts_nothing(
    queue, monkeypatch, twin
) -> None:
    import structlog

    from knowledge_ingest import db
    from knowledge_ingest.zombie_recovery import recover_zombie_jobs

    app, pool, conn, org_id = queue
    artifact = await _current_version(conn, org_id)

    @asynccontextmanager
    async def _admin():
        yield conn

    monkeypatch.setattr(db, "cross_org_admin_connection", _admin)
    lock = f"{org_id}:docs:page:{artifact}"
    stuck = await _stuck_job_and_twin(
        app,
        pool,
        "enrich_document_bulk",
        lock,
        {"artifact_id": artifact, "batch_offset": 0},
        {"artifact_id": artifact, **twin},
    )

    with structlog.testing.capture_logs() as logs:
        await recover_zombie_jobs(app)

    assert await _status(pool, stuck) == "aborted"
    assert await _enrichment_state(conn, artifact) == ("pending", None)
    assert not [e for e in logs if e["log_level"] == "error"]


async def test_a_stuck_job_whose_lock_holder_builds_something_else_fails_the_document(
    queue, monkeypatch
) -> None:
    import structlog

    from knowledge_ingest import db
    from knowledge_ingest.zombie_recovery import recover_zombie_jobs

    app, pool, conn, org_id = queue
    artifact = await _current_version(conn, org_id)

    @asynccontextmanager
    async def _admin():
        yield conn

    monkeypatch.setattr(db, "cross_org_admin_connection", _admin)
    stuck = await _stuck_job_and_twin(
        app,
        pool,
        "enrich_document_bulk",
        f"shared-{artifact}",
        {"artifact_id": artifact},
        {"artifact_id": str(uuid.uuid4())},
    )

    with structlog.testing.capture_logs() as logs:
        await recover_zombie_jobs(app)

    assert await _status(pool, stuck) == "failed"
    assert await _enrichment_state(conn, artifact) == ("failed", "stuck_job_not_covered")
    assert [e["event"] for e in logs if e["log_level"] == "error"] == [
        "derived_product_failed",
        "zombie_work_not_covered",
    ]
