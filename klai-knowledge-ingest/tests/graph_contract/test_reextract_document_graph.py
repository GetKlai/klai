"""scripts/reextract_document_graph.py against a real Postgres and a real FalkorDB.

Only the job queue is a fake: the script queues through graph_refresh like a
user-requested reindex, and the test records what reached the queue.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest import consistency_reconcile, enrichment_tasks, pg_store
from scripts import reextract_document_graph
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph


@pytest.fixture
def script(graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch):
    conn, insert = tenant_rows

    @asynccontextmanager
    async def _tenant(_org_id: str):
        yield conn

    @asynccontextmanager
    async def _app():
        yield None

    queued = MagicMock()
    queued.configure.return_value.defer_async = AsyncMock()
    monkeypatch.setattr(reextract_document_graph, "tenant_scoped_connection", _tenant)
    monkeypatch.setattr(reextract_document_graph, "_procrastinate_app", _app)
    monkeypatch.setattr(
        enrichment_tasks, "get_app", lambda: SimpleNamespace(ingest_graphiti_episode=queued)
    )
    return SimpleNamespace(
        conn=conn, insert=insert, defer=queued.configure.return_value.defer_async
    )


async def _lost_document(contract: Contract, insert) -> tuple[str, str]:
    """A finished extraction whose second episode the graph lost, after a finished one."""
    await seed_graph(contract, ["previous", "kept"], {"previous-fact": ["previous"]})
    active = await insert(
        {
            "document_text": "Text of the current version.",
            "graphiti_episode_ids": ["kept", "lost"],
            "graphiti_episode_id": "kept",
            "graphiti_episode_complete": True,
            "graphiti_extraction_version": 2,
        },
        active=True,
    )
    previous = await insert(
        {"graphiti_episode_ids": ["previous"], "graphiti_episode_complete": True},
        active=False,
        superseded_by=active,
    )
    return active, previous


async def _extra(conn, artifact_id: str) -> dict:
    return json.loads(
        await conn.fetchval(
            "SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact_id
        )
    )


async def test_dry_run_reports_and_changes_nothing(
    graph_helpers: Contract, script, capsys: pytest.CaptureFixture
) -> None:
    active, _ = await _lost_document(graph_helpers, script.insert)
    before = (await _extra(script.conn, active), await stored_graph(graph_helpers))

    assert await reextract_document_graph.run(graph_helpers.group_id, active, execute=False) == 0

    assert (await _extra(script.conn, active), await stored_graph(graph_helpers)) == before
    script.defer.assert_not_awaited()
    out = capsys.readouterr().out
    assert "active: True" in out
    assert "missing_in_graph: ['lost']" in out


async def test_execute_marks_the_row_not_extracted_and_queues_one_job(
    graph_helpers: Contract, script
) -> None:
    active, _ = await _lost_document(graph_helpers, script.insert)

    assert await reextract_document_graph.run(graph_helpers.group_id, active, execute=True) == 0

    extra = await _extra(script.conn, active)
    assert extra["graphiti_episode_complete"] is False
    assert extra["graphiti_episode_ids"] == ["kept"]
    current = await pg_store.get_current_graph_episode_uuids_for_org(
        script.conn, graph_helpers.group_id
    )
    assert current == {"kept", "previous"}
    script.defer.assert_awaited_once()
    assert script.defer.await_args.kwargs["artifact_id"] == active
    assert script.defer.await_args.kwargs["replace_stale"] is True


_TEXT = {"document_text": "Text of the current version."}
_REFUSED = {
    "closed": (
        {**_TEXT, "graphiti_episode_ids": ["lost"], "graphiti_episode_complete": True},
        False,
    ),
    "all-present": (
        {**_TEXT, "graphiti_episode_ids": ["kept"], "graphiti_episode_complete": True},
        True,
    ),
    # An extraction is queued, running, failed or quarantined here: the job's own state.
    "unfinished": (
        {**_TEXT, "graphiti_episode_ids": ["lost"], "graphiti_episode_complete": False},
        True,
    ),
    # The job aborts without text and would leave the row unfinished with no job.
    "no-text": ({"graphiti_episode_ids": ["lost"], "graphiti_episode_complete": True}, True),
    # The list is authoritative when its key exists, even empty: nothing is recorded.
    "empty-list-wins": (
        {
            **_TEXT,
            "graphiti_episode_ids": [],
            "graphiti_episode_id": "lost",
            "graphiti_episode_complete": True,
        },
        True,
    ),
}


@pytest.mark.parametrize("case", sorted(_REFUSED))
async def test_refuses_what_is_not_a_lost_finished_extraction(
    graph_helpers: Contract, script, case: str, capsys: pytest.CaptureFixture
) -> None:
    await seed_graph(graph_helpers, ["kept"], {"kept-fact": ["kept"]})
    extra, active = _REFUSED[case]
    artifact = await script.insert(extra, active=active)
    before = await _raw_extra(script.conn, artifact)

    assert await reextract_document_graph.run(graph_helpers.group_id, artifact, execute=True) == 2

    assert await _raw_extra(script.conn, artifact) == before
    script.defer.assert_not_awaited()
    if case == "empty-list-wins":
        # The same reading as the graph job and the nightly check: nothing recorded.
        assert "recorded_episode_ids: []" in capsys.readouterr().out


async def _raw_extra(conn, artifact_id: str) -> str:
    return await conn.fetchval(
        "SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact_id
    )


async def test_a_failed_queue_puts_the_row_back_so_the_loss_stays_visible(
    graph_helpers: Contract, script
) -> None:
    active, _ = await _lost_document(graph_helpers, script.insert)
    before = await _raw_extra(script.conn, active)
    script.defer.side_effect = ConnectionError("queue unavailable")

    assert await reextract_document_graph.run(graph_helpers.group_id, active, execute=True) == 1

    assert await _raw_extra(script.conn, active) == before
    report = await consistency_reconcile.lost_graph_extractions(
        script.conn, {graph_helpers.group_id}
    )
    assert report["graph_lost_extractions"] == 1
