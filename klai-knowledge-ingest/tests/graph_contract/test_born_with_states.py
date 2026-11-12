"""A document version is born with its product states, and an ingest that dies says so.

Real Postgres, as the other graph-contract tests. The version is created by
``pg_store.create_artifact``, the one place the service inserts artifact rows,
and the stale-pending reaper runs its real query.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager

import pytest
import structlog

from knowledge_ingest import pg_store, stale_pending_artifact_reaper
from knowledge_ingest.derived_state import ProductState, set_product_state
from tests.graph_contract.test_convert_document_states import store  # noqa: F401 - fixture


async def _version(store, path: str = "page") -> str:  # noqa: F811 - fixture name
    return await pg_store.create_artifact(
        store.conn,
        store.org_id,
        "docs",
        path,
        "observed",
        "factual",
        0,
        None,
        1,
        pg_store._SENTINEL,
        extra={"document_text": "Text."},
        content_hash="h",
        index_status="pending",
    )


async def _index_status(store, artifact: str) -> str:  # noqa: F811
    return await store.conn.fetchval(
        "SELECT index_status FROM knowledge.artifacts WHERE id = $1", artifact
    )


async def test_a_new_version_carries_both_states_pending(store) -> None:  # noqa: F811
    artifact = await _version(store)

    extra = json.loads(
        await store.conn.fetchval(
            "SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact
        )
    )

    assert (extra["enrichment_state"], extra["graph_state"]) == ("pending", "pending")
    assert extra["enrichment_state_at"] and extra["graph_state_at"]
    assert extra["document_text"] == "Text."


async def _reap(store, monkeypatch) -> list[dict]:  # noqa: F811
    @asynccontextmanager
    async def _admin():
        yield store.conn

    monkeypatch.setattr(stale_pending_artifact_reaper, "cross_org_admin_connection", _admin)
    return await stale_pending_artifact_reaper.reap_stale_pending_artifacts()


_LONG_AGO = int(time.time()) - 3600


async def _waiting_since(store, artifact: str, *, created=None, **state_at) -> None:  # noqa: F811
    """Age what is waiting: the row's creation, and each named product's pending."""
    if created is not None:
        await store.conn.execute(
            "UPDATE knowledge.artifacts SET created_at = $2 WHERE id = $1", artifact, created
        )
    await store.conn.execute(
        "UPDATE knowledge.artifacts SET extra = extra || $2::jsonb WHERE id = $1",
        artifact,
        json.dumps({f"{product}_state_at": at for product, at in state_at.items()}),
    )


def _failures(logs, *artifacts: str) -> list[tuple[str, str, str]]:
    # The reaper spans every organisation; other tests' rows are not ours.
    return sorted(
        (e["artifact_id"], e["product"], e["reason"])
        for e in logs
        if e["event"] == "derived_product_failed" and e["artifact_id"] in artifacts
    )


async def test_an_ingest_that_died_ends_with_its_products_failed(store, monkeypatch) -> None:  # noqa: F811
    died = await _version(store, "died")
    graph_done = await _version(store, "graph-done")
    await set_product_state(store.conn, graph_done, "graph", "done")
    for artifact in (died, graph_done):
        await _waiting_since(
            store, artifact, created=_LONG_AGO, enrichment=_LONG_AGO, graph=_LONG_AGO
        )

    with structlog.testing.capture_logs() as logs:
        await _reap(store, monkeypatch)

    assert await _index_status(store, died) == "failed"
    for product in ("enrichment", "graph"):
        assert await store.state(died, product) == ProductState("failed", "interrupted")
    assert await store.state(graph_done, "graph") == ProductState("done")
    assert await store.state(graph_done, "enrichment") == ProductState("failed", "interrupted")
    assert _failures(logs, died, graph_done) == sorted(
        [
            (died, "enrichment", "interrupted"),
            (died, "graph", "interrupted"),
            (graph_done, "enrichment", "interrupted"),
        ]
    )
    assert not [e for e in logs if e["event"] == "derived_state_transition_refused"]


async def _synced(store, path: str) -> str:  # noqa: F811
    artifact = await _version(store, path)
    await store.conn.execute(
        "UPDATE knowledge.artifacts SET index_status = 'synced' WHERE id = $1", artifact
    )
    return artifact


async def test_a_synced_version_whose_enrichment_waits_with_no_job_fails_it(
    store,  # noqa: F811
    monkeypatch,
) -> None:
    """The request died after the text was written and before the work was queued."""
    artifact = await _synced(store, "page")
    await _waiting_since(store, artifact, enrichment=_LONG_AGO)

    with structlog.testing.capture_logs() as logs:
        await _reap(store, monkeypatch)

    assert await store.state(artifact, "enrichment") == ProductState("failed", "interrupted")
    assert await store.state(artifact, "graph") == ProductState("pending")  # waiting briefly
    assert await _index_status(store, artifact) == "synced"
    assert _failures(logs, artifact) == [(artifact, "enrichment", "interrupted")]


@pytest.mark.parametrize(
    ("product", "task", "waiting", "expected"),
    [
        ("enrichment", "enrich_document_bulk", _LONG_AGO, ProductState("pending")),
        ("enrichment", None, int(time.time()), ProductState("pending")),
        ("graph", "ingest_graphiti_episode", _LONG_AGO, ProductState("pending")),
        ("graph", None, _LONG_AGO, ProductState("failed", "interrupted")),
    ],
    ids=["enrichment-job-queued", "enrichment-waiting-briefly", "graph-job-queued", "graph-no-job"],
)
async def test_a_waiting_product_fails_only_without_a_job_and_past_the_cutoff(
    store,  # noqa: F811
    monkeypatch,
    product,
    task,
    waiting,
    expected,
) -> None:
    artifact = await _synced(store, "page")
    # An old version put back to pending just now (a reindex, a rebuild) is
    # measured from its product, not from when the version was created.
    await _waiting_since(store, artifact, created=_LONG_AGO, **{product: waiting})
    if task:
        await store.job(task, artifact)

    await _reap(store, monkeypatch)

    assert await store.state(artifact, product) == expected


async def test_a_knowledge_base_under_an_operation_keeps_its_versions_waiting(
    store,  # noqa: F811
    monkeypatch,
) -> None:
    """A rebuild sets enrichment pending and works on it with no job of its own,
    for longer than the cutoff on a large document; it holds the knowledge
    base's operation lock the whole time."""
    import asyncpg

    from tests.graph_contract.conftest import _PG_DSN

    artifact = await _synced(store, "page")
    await _waiting_since(store, artifact, enrichment=_LONG_AGO)
    rebuild = await asyncpg.connect(_PG_DSN)
    try:
        await rebuild.execute(
            "SELECT pg_advisory_lock(hashtextextended($1, 0))",
            f"kb-operation:{store.org_id}:docs",
        )
        await _reap(store, monkeypatch)
    finally:
        await rebuild.close()

    assert await store.state(artifact, "enrichment") == ProductState("pending")


async def test_a_product_queued_again_while_the_reaper_runs_keeps_waiting(
    store,  # noqa: F811
    monkeypatch,
) -> None:
    """Between the reaper's read and its write a reindex queues the work again.

    Its ``failed`` must not land on the new wait: the job's later ``done``
    would be refused.
    """
    artifact = await _synced(store, "page")
    await _waiting_since(store, artifact, enrichment=_LONG_AGO)

    async def _queued_again_meanwhile(conn, org_id: str, kb_slug: str) -> bool:
        await set_product_state(conn, artifact, "enrichment", "pending")
        return False

    monkeypatch.setattr(stale_pending_artifact_reaper, "operation_running", _queued_again_meanwhile)
    await _reap(store, monkeypatch)

    assert await store.state(artifact, "enrichment") == ProductState("pending")
