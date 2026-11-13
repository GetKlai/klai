"""Tests for the requeue_failed_artifacts operator script.

Mirrors the mock style of ``tests/test_stale_pending_artifact_reaper.py``:
``cross_org_admin_connection`` and the Procrastinate app bootstrap are both
patched out, so no real Postgres/psycopg connection is ever required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from knowledge_ingest.scripts import requeue_failed_artifacts as script

_SENTINEL = 253402300800


def _row(
    artifact_id: str,
    *,
    org_id: str = "org1",
    kb_slug: str = "kb1",
    path: str = "a.md",
    created_at: int = 1700000000,
    index_status: str = "synced",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": path,
        "content_type": "kb_article",
        "belief_time_start": 1700000001,
        "created_at": created_at,
        "index_status": index_status,
        "has_text": True,
    }


def _db_row(artifact_id: str, extra: dict | None = None, **kwargs) -> dict:
    """A selected row as Postgres returns it: the reader decides from ``extra``."""
    extra = {"document_text": "Text.", "enrichment_state": "failed"} if extra is None else extra
    return {**_row(artifact_id, **kwargs), "extra": extra}


def _make_conn(rows: list[dict]) -> MagicMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchrow = AsyncMock(return_value={"org_id": "org1", "kb_slug": "kb1"})
    conn.execute = AsyncMock()
    return conn


class _FakeBulkTask:
    """Stand-in for ``proc_app.enrich_document_bulk``."""

    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.configure_calls: list[dict] = []
        self.defer_calls: list[str] = []
        self._fail_for = fail_for or set()

    def configure(self, **kwargs: object) -> _FakeBulkTask:
        self.configure_calls.append(kwargs)
        return self

    async def defer_async(self, *, artifact_id: str) -> None:
        self.defer_calls.append(artifact_id)
        if artifact_id in self._fail_for:
            from procrastinate.exceptions import AlreadyEnqueued

            raise AlreadyEnqueued()


class _FakeApp:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.enrich_document_bulk = _FakeBulkTask(fail_for=fail_for)


@asynccontextmanager
async def _conn_ctx(conn: MagicMock):
    yield conn


@asynccontextmanager
async def _app_ctx(app: _FakeApp):
    yield app


# ---------------------------------------------------------------------------
# (a) selection SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_select_failed_artifacts_uses_expected_filters_no_scoping():
    conn = _make_conn([])

    await script._select_failed_artifacts(conn, org_id=None, kb_slug=None, limit=500)

    assert conn.fetch.await_count == 1
    sql, *params = conn.fetch.call_args.args
    assert "index_status = 'failed'" in sql
    assert "belief_time_end = $1" in sql
    assert "$2::text IS NULL OR a.org_id = $2" in sql
    assert "$3::text IS NULL OR a.kb_slug = $3" in sql
    assert "ORDER BY a.created_at ASC" in sql
    assert "LIMIT $4" in sql
    assert params == [_SENTINEL, None, None, 500, "enrichment", None, "deleting"]


@pytest.mark.asyncio
async def test_select_failed_artifacts_applies_org_and_kb_scoping():
    conn = _make_conn([])

    await script._select_failed_artifacts(conn, org_id="org1", kb_slug="kb1", limit=10)

    _sql, *params = conn.fetch.call_args.args
    assert params == [_SENTINEL, "org1", "kb1", 10, "enrichment", None, "deleting"]


@pytest.mark.asyncio
async def test_select_failed_artifacts_selects_what_the_reader_calls_failed():
    """A failed raw write next to a finished enrichment is not a missing
    enrichment; a legacy upload whose index_status carried the failure is."""
    conn = _make_conn(
        [
            _db_row("enrichment-failed"),
            _db_row("raw-write-failed", {"enrichment_state": "done"}, index_status="failed"),
            _db_row("legacy-upload", {}, index_status="failed"),
            _db_row("legacy-connector", {"source_connector_id": "c1"}, index_status="failed"),
        ]
    )

    result = await script._select_failed_artifacts(conn, org_id=None, kb_slug=None, limit=500)

    assert [row["artifact_id"] for row in result] == ["enrichment-failed", "legacy-upload"]


# ---------------------------------------------------------------------------
# (b) dry-run defers nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_prints_summary_and_never_requeues(capsys):
    rows = [_db_row("a1"), _db_row("a2", org_id="org2", kb_slug="kb2")]
    conn = _make_conn(rows)

    with (
        patch.object(script, "cross_org_admin_connection", return_value=_conn_ctx(conn)),
        patch.object(script, "_requeue", new_callable=AsyncMock) as mock_requeue,
    ):
        await script.main(dry_run=True, limit=1000, org_id=None, kb_slug=None)

    mock_requeue.assert_not_called()
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "2 failed artifact(s)" in out
    assert "a1" in out
    assert "a2" in out


@pytest.mark.asyncio
async def test_no_artifacts_selected_returns_early_without_requeue():
    conn = _make_conn([])

    with (
        patch.object(script, "cross_org_admin_connection", return_value=_conn_ctx(conn)),
        patch.object(script, "_requeue", new_callable=AsyncMock) as mock_requeue,
    ):
        await script.main(dry_run=False, limit=1000, org_id=None, kb_slug=None)

    mock_requeue.assert_not_called()


# ---------------------------------------------------------------------------
# (c) execute defers with the requeue-specific queueing_lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_defers_enrich_document_bulk_with_requeue_lock():
    rows = [_db_row("a1"), _db_row("a2")]
    conn = _make_conn(rows)
    fake_app = _FakeApp()

    with (
        patch.object(script, "cross_org_admin_connection", side_effect=lambda: _conn_ctx(conn)),
        patch.object(script, "_procrastinate_app", return_value=_app_ctx(fake_app)),
    ):
        await script.main(dry_run=False, limit=1000, org_id=None, kb_slug=None)

    task = fake_app.enrich_document_bulk
    assert task.defer_calls == ["a1", "a2"]
    assert task.configure_calls == [
        {"queueing_lock": "requeue:a1"},
        {"queueing_lock": "requeue:a2"},
    ]


@pytest.mark.asyncio
async def test_execute_emits_done_summary_log_event():
    rows = [_db_row("a1")]
    conn = _make_conn(rows)
    fake_app = _FakeApp()

    with (
        patch.object(script, "cross_org_admin_connection", side_effect=lambda: _conn_ctx(conn)),
        patch.object(script, "_procrastinate_app", return_value=_app_ctx(fake_app)),
        structlog.testing.capture_logs() as captured,
    ):
        await script.main(dry_run=False, limit=1000, org_id=None, kb_slug=None)

    done_events = [e for e in captured if e["event"] == "requeue_failed_artifacts_done"]
    assert len(done_events) == 1
    assert done_events[0]["selected"] == 1
    assert done_events[0]["enqueued"] == 1
    assert done_events[0]["skipped_already_enqueued"] == 0


# ---------------------------------------------------------------------------
# (d) AlreadyEnqueued -> skipped, loop continues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_already_enqueued_is_skipped_and_does_not_stop_the_batch():
    rows = [_row("a1"), _row("a2"), _row("a3")]
    fake_app = _FakeApp(fail_for={"a2"})
    conn = _make_conn([])

    with (
        patch.object(script, "_procrastinate_app", return_value=_app_ctx(fake_app)),
        patch.object(script, "cross_org_admin_connection", side_effect=lambda: _conn_ctx(conn)),
    ):
        enqueued, skipped = await script._requeue(rows)

    assert fake_app.enrich_document_bulk.defer_calls == ["a1", "a2", "a3"]
    assert enqueued == 2
    assert skipped == 1


# ---------------------------------------------------------------------------
# (e) a failed graph is queued as the ingest route queues it
# ---------------------------------------------------------------------------


class _FakeGraphTask:
    def __init__(self) -> None:
        self.configure_calls: list[dict] = []
        self.defer_calls: list[dict] = []

    def configure(self, **kwargs: object) -> _FakeGraphTask:
        self.configure_calls.append(kwargs)
        return self

    async def defer_async(self, **kwargs: object) -> None:
        self.defer_calls.append(kwargs)


@pytest.mark.asyncio
async def test_a_failed_graph_is_queued_with_the_ingest_routes_job_arguments():
    rows = [_row("a1", path="docs/a.md"), {**_row("a2"), "has_text": False}]
    graph_task = _FakeGraphTask()
    fake_app = MagicMock(ingest_graphiti_episode=graph_task)
    conn = _make_conn([])

    with (
        patch.object(script, "_procrastinate_app", return_value=_app_ctx(fake_app)),
        patch.object(script, "cross_org_admin_connection", side_effect=lambda: _conn_ctx(conn)),
    ):
        enqueued, skipped = await script._requeue(rows, "graph")

    assert (enqueued, skipped) == (1, 0)
    assert graph_task.configure_calls == [{"lock": "graphiti:a1", "queueing_lock": "graphiti:a1"}]
    assert graph_task.defer_calls == [
        {
            "artifact_id": "a1",
            "org_id": "org1",
            "content_type": "kb_article",
            "belief_time_start": 1700000001,
            "kb_slug": "kb1",
            "path": "docs/a.md",
            "replace_stale": True,
        }
    ]
    # The version is moved back to pending through the one writer, and the
    # legacy index_status repair of an enrichment requeue is not applied.
    sql = " ".join(str(c.args[0]) for c in conn.fetchrow.await_args_list)
    assert "UPDATE knowledge.artifacts" in sql
    conn.execute.assert_not_awaited()
