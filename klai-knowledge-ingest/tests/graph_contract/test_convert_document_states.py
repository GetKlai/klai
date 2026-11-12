"""convert_document_states writes, once, the state that is true for every legacy row.

Real Postgres (the artifact rows and procrastinate's job table), a real
FalkorDB (the recorded episodes), and Qdrant's in-process mode for the vector
index (``AsyncQdrantClient(location=":memory:")``, the same engine the service's
other tests use, not a hand-written fake).
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest
import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from knowledge_ingest import org_config
from knowledge_ingest.derived_state import ProductState, product_state
from knowledge_ingest.scripts import convert_document_states as script
from tests.graph_contract.conftest import _PG_DSN, Contract, seed_graph
from tests.test_derived_state_reader import ENRICHMENT_CASES, GRAPH_CASES

_TEXT = "The handset restarts after the update. Settings stay as they were."


@pytest.fixture
async def store(graph_helpers: Contract, tenant_rows, monkeypatch, real_procrastinate):
    from procrastinate.schema import SchemaManager

    conn, insert = tenant_rows
    setup = await asyncpg.connect(_PG_DSN)
    try:
        if await setup.fetchval("SELECT to_regclass('public.procrastinate_jobs')") is None:
            await setup.execute(SchemaManager.get_schema())
    finally:
        await setup.close()
    client = AsyncQdrantClient(location=":memory:")
    await client.create_collection(
        "klai_knowledge",
        vectors_config={"vector_chunk": VectorParams(size=2, distance=Distance.COSINE)},
    )

    @asynccontextmanager
    async def _admin():
        yield conn

    monkeypatch.setattr(script, "cross_org_admin_connection", _admin)
    monkeypatch.setattr(script, "get_client", lambda: client)
    # The test settings switch enrichment off globally; a test that needs it
    # off for the organisation says so.
    monkeypatch.setattr(org_config, "is_enrichment_enabled", AsyncMock(return_value=True))
    org_id = graph_helpers.group_id
    job_ids: list[int] = []

    async def row(extra: dict, *, active: bool = True, index_status: str = "synced") -> str:
        artifact = await insert(extra, active=active, path=str(uuid.uuid4()))
        await conn.execute(
            "UPDATE knowledge.artifacts SET index_status = $2 WHERE id = $1", artifact, index_status
        )
        return artifact

    async def points(artifact: str, *, raw: int = 0, enriched: int = 0) -> None:
        payloads = [{"text": "t"}] * raw + [{"text": "t", "text_enriched": "c\n\nt"}] * enriched
        await client.upsert(
            "klai_knowledge",
            points=[
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"vector_chunk": [0.1, 0.2]},
                    payload={"org_id": org_id, "artifact_id": artifact, **payload},
                )
                for payload in payloads
            ],
        )

    async def job(task: str, artifact: str) -> None:
        job_ids.append(
            await conn.fetchval(
                "INSERT INTO procrastinate_jobs (queue_name, task_name, args) "
                "VALUES ('q', $1, $2::jsonb) RETURNING id",
                f"knowledge_ingest.enrichment_tasks.{task}",
                json.dumps({"artifact_id": artifact}),
            )
        )

    async def convert(*, execute: bool = True, batch_size: int = 500):
        return await script.convert_org(
            conn, client, org_id, execute=execute, batch_size=batch_size
        )

    async def state(artifact: str, product: str) -> ProductState:
        extra = json.loads(
            await conn.fetchval(
                "SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact
            )
        )
        return ProductState(extra.get(f"{product}_state"), extra.get(f"{product}_reason"))

    yield SimpleNamespace(
        conn=conn,
        org_id=org_id,
        contract=graph_helpers,
        row=row,
        points=points,
        job=job,
        convert=convert,
        state=state,
    )
    await conn.execute("DELETE FROM procrastinate_jobs WHERE id = ANY($1::bigint[])", job_ids)


# --- what the reader already knows is written as it is -----------------------


@pytest.mark.parametrize(
    "case",
    sorted(
        c
        for c, (_, expected) in GRAPH_CASES.items()
        if expected.state not in ("unknown", "pending") and c != "new state wins over old fields"
    ),
)
async def test_a_known_graph_shape_keeps_what_the_reader_said(store, case) -> None:
    extra, _ = GRAPH_CASES[case]
    said = product_state(extra, "graph")
    for episode in extra.get("graphiti_episode_ids") or [extra.get("graphiti_episode_id")]:
        if episode and not str(episode).startswith(("skipped:", "no-chunks")):
            await seed_graph(store.contract, [episode], {})
    artifact = await store.row(extra)

    await store.convert()

    assert await store.state(artifact, "graph") == said


@pytest.mark.parametrize(
    "case",
    sorted(
        c
        for c, (_, _, expected) in ENRICHMENT_CASES.items()
        if expected.state == "not_applicable" or expected.state == "failed"
    ),
)
async def test_a_known_enrichment_shape_keeps_what_the_reader_said(store, case) -> None:
    extra, index_status, said = ENRICHMENT_CASES[case]
    artifact = await store.row(extra, index_status=index_status)
    await store.points(artifact, raw=2)

    await store.convert()

    assert await store.state(artifact, "enrichment") == said


async def test_an_upload_whose_index_status_carried_its_enrichment_gets_todays_meaning(
    store,
) -> None:
    failed = await store.row({"document_text": _TEXT}, index_status="failed")
    await store.points(failed, raw=2)
    never_written = await store.row({"document_text": _TEXT}, index_status="pending")
    connector = await store.row(
        {"document_text": _TEXT, "source_connector_id": "c1"}, index_status="failed"
    )

    await store.convert()

    statuses = {
        r["id"]: r["index_status"]
        for r in await store.conn.fetch(
            "SELECT id::text AS id, index_status FROM knowledge.artifacts "
            "WHERE id = ANY($1::uuid[])",
            [failed, never_written, connector],
        )
    }
    # Raw chunks were written: the text is searchable, its enrichment failed.
    assert statuses[failed] == "synced"
    assert await store.state(failed, "enrichment") == ProductState("failed", "legacy")
    # No points at all: the raw write itself never landed.
    assert statuses[never_written] == "failed"
    # A connector row's index_status always had today's meaning.
    assert statuses[connector] == "failed"


# --- where the reader says unknown, the truth is read where it lives ----------


async def test_an_unknown_graph_is_resolved_from_the_row_and_todays_policy(store) -> None:
    navigation = "\n".join(f"* [Page {n}](https://example.com/{n})" for n in range(12))
    never_built = await store.row({"document_text": _TEXT})
    held_out = await store.row({"document_text": navigation})
    no_text = await store.row({})
    interrupted = await store.row(
        {
            "document_text": _TEXT,
            "graphiti_episode_ids": ["ep-x"],
            "graphiti_episode_complete": False,
        }
    )
    running = await store.row({"document_text": _TEXT})
    await store.job("ingest_graphiti_episode", running)
    closed = await store.row({"document_text": navigation}, active=False)

    await store.convert()

    assert await store.state(never_built, "graph") == ProductState("failed", script.MISSING)
    assert await store.state(held_out, "graph") == ProductState("not_applicable", "navigation_page")
    assert await store.state(no_text, "graph") == ProductState("failed", "no_stored_text")
    assert await store.state(interrupted, "graph") == ProductState("failed", script.MISSING)
    assert await store.state(running, "graph") == ProductState("pending")
    # Not finished, so the current-content walk passes it over as it does today,
    # even where today's policy would have held the version out of the graph.
    assert await store.state(closed, "graph") == ProductState("failed", script.MISSING)


async def test_a_recorded_extraction_the_graph_lost_stays_done_and_is_counted(store) -> None:
    await seed_graph(store.contract, ["ep-kept"], {})
    lost = await store.row({"document_text": _TEXT, "graphiti_episode_ids": ["ep-kept", "ep-lost"]})

    counts = await store.convert()

    assert await store.state(lost, "graph") == ProductState("done")
    assert counts[("check", "done_with_episodes_missing", None)] == 1


async def test_an_unknown_enrichment_is_read_from_the_vector_index(store, monkeypatch) -> None:
    connector = {"document_text": _TEXT, "source_connector_id": "c1"}
    enriched = await store.row(connector)
    await store.points(enriched, enriched=3)
    raw = await store.row(connector)
    await store.points(raw, raw=3)
    partial = await store.row(connector)
    await store.points(partial, raw=1, enriched=2)
    too_large = await store.row({**connector, "docling_chunk_count": 100_000})
    await store.points(too_large, raw=3)
    no_text = await store.row({"source_connector_id": "c1"})
    await store.points(no_text, raw=1)
    running = await store.row(connector)
    await store.job("enrich_document_bulk", running)
    closed = await store.row(connector, active=False)

    await store.convert()

    assert await store.state(enriched, "enrichment") == ProductState("done")
    assert await store.state(raw, "enrichment") == ProductState("failed", script.MISSING)
    assert await store.state(partial, "enrichment") == ProductState("failed", script.MISSING)
    assert await store.state(too_large, "enrichment") == ProductState(
        "not_applicable", "too_many_chunks"
    )
    assert await store.state(no_text, "enrichment") == ProductState("failed", "no_stored_text")
    assert await store.state(running, "enrichment") == ProductState("pending")
    assert await store.state(closed, "enrichment") == ProductState(
        "not_applicable", script.NOT_CURRENT
    )


async def test_enrichment_switched_off_for_the_organisation_is_not_applicable(
    store, monkeypatch
) -> None:
    monkeypatch.setattr(org_config, "is_enrichment_enabled", AsyncMock(return_value=False))
    raw = await store.row({"document_text": _TEXT, "source_connector_id": "c1"})
    await store.points(raw, raw=2)

    await store.convert()

    assert await store.state(raw, "enrichment") == ProductState(
        "not_applicable", "disabled_for_organisation"
    )


# --- how it runs ---------------------------------------------------------------


async def test_the_preview_writes_nothing_and_counts_what_the_run_writes(store) -> None:
    artifact = await store.row({"document_text": _TEXT})
    before = await store.conn.fetchval(
        "SELECT extra::text || index_status FROM knowledge.artifacts WHERE id = $1", artifact
    )

    preview = await store.convert(execute=False)

    assert before == await store.conn.fetchval(
        "SELECT extra::text || index_status FROM knowledge.artifacts WHERE id = $1", artifact
    )
    assert await script.remaining(store.conn, store.org_id) == {"enrichment": 1, "graph": 1}
    assert await store.convert() == preview
    assert await script.remaining(store.conn, store.org_id) == {"enrichment": 0, "graph": 0}


async def test_a_second_run_changes_nothing_and_never_touches_an_explicit_state(store) -> None:
    live = await store.row({"graph_state": "done", "enrichment_state": "pending"})
    legacy = await store.row({"document_text": _TEXT})
    await store.convert()
    snapshot = await store.conn.fetch(
        "SELECT id, extra::text FROM knowledge.artifacts WHERE id = ANY($1::uuid[]) ORDER BY id",
        [live, legacy],
    )

    assert await store.convert() == {}
    assert snapshot == await store.conn.fetch(
        "SELECT id, extra::text FROM knowledge.artifacts WHERE id = ANY($1::uuid[]) ORDER BY id",
        [live, legacy],
    )
    assert await store.state(live, "enrichment") == ProductState("pending")


async def test_an_interrupted_run_continues_where_it_stopped(store, monkeypatch) -> None:
    rows = [await store.row({"document_text": _TEXT}) for _ in range(3)]
    real_write = script._write
    writes = 0

    async def _crash_after_two(*args):
        nonlocal writes
        if writes == 2:
            raise ConnectionError("connection lost")
        writes += 1
        await real_write(*args)

    monkeypatch.setattr(script, "_write", _crash_after_two)
    with pytest.raises(ConnectionError):
        await store.convert(batch_size=1)
    monkeypatch.setattr(script, "_write", real_write)

    counts = await store.convert(batch_size=1)

    assert sum(counts.values()) == 4  # the six writes, less the two that landed
    for artifact in rows:
        assert await store.state(artifact, "graph") == ProductState("failed", script.MISSING)


async def test_the_conversion_logs_no_failure_event_per_row(store) -> None:
    for _ in range(3):
        await store.row({"document_text": _TEXT})

    with structlog.testing.capture_logs() as logs:
        await store.convert()

    assert [e["event"] for e in logs] == ["document_states_converted"]


async def test_a_run_without_the_marker_or_without_the_lock_writes_nothing(
    store, monkeypatch
) -> None:
    from knowledge_ingest.operations import OperationBusy

    artifact = await store.row({"document_text": _TEXT})
    monkeypatch.setattr("sys.argv", ["convert_document_states", "--execute"])
    monkeypatch.delenv("INGEST_OK", raising=False)
    with pytest.raises(SystemExit) as refused:
        script.main()
    assert refused.value.code == 2

    @asynccontextmanager
    async def _busy(*_args, **_kwargs):
        raise OperationBusy("another operation is running")
        yield

    monkeypatch.setattr(script, "kb_operation", _busy)
    with pytest.raises(OperationBusy):
        await script.run(store.org_id, execute=True)

    assert await store.state(artifact, "graph") == ProductState(None)
