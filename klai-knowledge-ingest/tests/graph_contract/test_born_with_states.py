"""A document version is born with its product states, and an ingest that dies says so.

Real Postgres, as the other graph-contract tests. The version is created by
``pg_store.create_artifact``, the one place the service inserts artifact rows,
and the stale-pending reaper runs its real query.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager

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
    return await stale_pending_artifact_reaper.reap_stale_pending_artifacts(
        stale_after_seconds=0, limit=500
    )


async def test_an_ingest_that_died_ends_with_its_products_failed(store, monkeypatch) -> None:  # noqa: F811
    died = await _version(store, "died")
    graph_done = await _version(store, "graph-done")
    await set_product_state(store.conn, graph_done, "graph", "done")
    await store.conn.execute(
        "UPDATE knowledge.artifacts SET created_at = $1 WHERE org_id = $2",
        int(time.time()) - 3600,
        store.org_id,
    )

    with structlog.testing.capture_logs() as logs:
        await _reap(store, monkeypatch)

    assert await _index_status(store, died) == "failed"
    for product in ("enrichment", "graph"):
        assert await store.state(died, product) == ProductState("failed", "ingest_interrupted")
    assert await store.state(graph_done, "graph") == ProductState("done")
    assert await store.state(graph_done, "enrichment") == ProductState(
        "failed", "ingest_interrupted"
    )
    failed = [
        (e["artifact_id"], e["product"], e["reason"])
        for e in logs
        # The reaper spans every organisation; other tests' rows are not ours.
        if e["event"] == "derived_product_failed" and e["artifact_id"] in (died, graph_done)
    ]
    assert sorted(failed) == sorted(
        [
            (died, "enrichment", "ingest_interrupted"),
            (died, "graph", "ingest_interrupted"),
            (graph_done, "enrichment", "ingest_interrupted"),
        ]
    )
    assert not [e for e in logs if e["event"] == "derived_state_transition_refused"]


async def test_a_graph_job_still_queued_keeps_the_graph_pending(store, monkeypatch) -> None:  # noqa: F811
    artifact = await _version(store)
    await store.job("ingest_graphiti_episode", artifact)
    await store.conn.execute(
        "UPDATE knowledge.artifacts SET created_at = 1 WHERE id = $1", artifact
    )

    await _reap(store, monkeypatch)

    assert await store.state(artifact, "graph") == ProductState("pending")
    assert await store.state(artifact, "enrichment") == ProductState("failed", "ingest_interrupted")
