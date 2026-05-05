"""SPEC-CRAWLER-006 REQ-06: tests for the sync_run reaper.

Each test calls :meth:`SyncRunReaper.tick` directly rather than starting
the loop — the loop is just ``while True: await tick(); await sleep()``
with cancellation handling, which is mostly asyncio plumbing not worth
mocking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.enums import SyncStatus
from app.services.sync_run_reaper import SyncRunReaper


def _row(
    *,
    started_at: datetime,
    remote_job_id: str | None = "abc123",
    status: str = SyncStatus.RUNNING,
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.connector_id = uuid.uuid4()
    row.status = status
    row.started_at = started_at
    row.completed_at = None
    row.documents_total = 0
    row.documents_ok = 0
    row.documents_failed = 0
    row.bytes_processed = 0
    row.error_details = None
    row.cursor_state = (
        {"remote_job_id": remote_job_id, "remote_status": "queued"}
        if remote_job_id
        else None
    )
    row.quality_status = None
    return row


def _make_reaper(
    *,
    candidate_rows: list[MagicMock],
    status_responses: list[dict | Exception] | None = None,
    finalize_after_h: float = 24.0,
    force_fail_after_d: float = 7.0,
) -> tuple[SyncRunReaper, MagicMock, MagicMock, MagicMock, dict]:
    """Build a reaper wired to mocks. Returns
    (reaper, crawl_client, session_mock, portal_client, registry).
    """
    crawl_client = MagicMock()
    if status_responses is not None:
        crawl_client.crawl_sync_status = AsyncMock(side_effect=status_responses)
    else:
        crawl_client.crawl_sync_status = AsyncMock(
            return_value={"job_id": "abc123", "status": "running",
                          "pages_done": 0, "pages_total": 0, "error": None},
        )
    crawl_client.crawl_sync_cancel = AsyncMock()  # MUST NOT be called.

    # Registry maps row.id -> row mock for session.get inside _finalise_*.
    registry: dict = {r.id: r for r in candidate_rows}

    # Two distinct session-mock factories: one for the SELECT (returns
    # the candidate list), one for the per-row UPDATE inside _finalise_*.
    def _make_session() -> MagicMock:
        sess = MagicMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        # session.execute(...) returns a Result; .scalars().all() -> rows.
        result = MagicMock()
        result.scalars = MagicMock(return_value=MagicMock(all=lambda: candidate_rows))
        sess.execute = AsyncMock(return_value=result)
        sess.get = AsyncMock(side_effect=lambda model, row_id, **kwargs: registry.get(row_id))
        sess.commit = AsyncMock()
        return sess

    session_maker = MagicMock(side_effect=_make_session)

    portal_client = MagicMock()
    portal_client.report_sync_status = AsyncMock()

    reaper = SyncRunReaper(
        crawl_sync_client=crawl_client,
        session_maker=session_maker,
        portal_client=portal_client,
        tick_seconds=1.0,
        finalize_after_seconds=finalize_after_h * 3600.0,
        force_fail_after_seconds=force_fail_after_d * 86400.0,
    )
    return reaper, crawl_client, session_maker, portal_client, registry


class TestReaperFinalisesTerminalRemote:
    """AC-06.1: a remote job that has terminated is reflected in the local row."""

    @pytest.mark.asyncio
    async def test_completed_remote_writes_terminal_state_and_reports(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)
        row = _row(started_at=old)
        reaper, crawl_client, _, portal, _ = _make_reaper(
            candidate_rows=[row],
            status_responses=[
                {"job_id": "abc123", "status": "completed",
                 "pages_done": 100, "pages_total": 100, "error": None},
            ],
        )

        finalised = await reaper.tick()

        assert finalised == 1
        assert row.status == SyncStatus.COMPLETED
        assert row.documents_ok == 100
        assert row.documents_total == 100
        assert row.error_details is None

        portal.report_sync_status.assert_awaited_once()
        assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.COMPLETED
        # Reaper MUST NOT cancel the upstream job.
        crawl_client.crawl_sync_cancel.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_remote_writes_failed_with_error(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)
        row = _row(started_at=old)
        reaper, _, _, portal, _ = _make_reaper(
            candidate_rows=[row],
            status_responses=[
                {"job_id": "abc123", "status": "failed",
                 "pages_done": 5, "pages_total": 100, "error": "internal_crash"},
            ],
        )

        finalised = await reaper.tick()

        assert finalised == 1
        assert row.status == SyncStatus.FAILED
        assert row.documents_ok == 5
        assert row.documents_failed == 95
        assert row.error_details
        assert row.error_details[0]["error"] == "internal_crash"
        assert row.error_details[0]["service"] == "knowledge-ingest"
        portal.report_sync_status.assert_awaited_once()


class TestReaper404IsRemoteJobLost:
    """AC-06.2: 404 from knowledge-ingest -> remote_job_lost."""

    @pytest.mark.asyncio
    async def test_404_marks_failed_with_remote_job_lost(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)
        row = _row(started_at=old)
        fake_404 = httpx.HTTPStatusError(
            "404 not found",
            request=httpx.Request("GET", "http://knowledge-ingest/.../status"),
            response=httpx.Response(404, text="job not found"),
        )
        reaper, _, _, portal, _ = _make_reaper(
            candidate_rows=[row],
            status_responses=[fake_404],
        )

        finalised = await reaper.tick()

        assert finalised == 1
        assert row.status == SyncStatus.FAILED
        assert row.error_details[0]["error"] == "remote_job_lost"
        assert row.error_details[0]["service"] == "knowledge-ingest"
        portal.report_sync_status.assert_awaited_once()
        assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.FAILED


class TestReaperLeavesYoungRunningRowsAlone:
    """AC-06.3: a still-running remote on a row younger than 7d stays."""

    @pytest.mark.asyncio
    async def test_running_under_7d_is_left_alone(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)  # >24h, <7d
        row = _row(started_at=old)
        reaper, _, _, portal, _ = _make_reaper(
            candidate_rows=[row],
            status_responses=[
                {"job_id": "abc123", "status": "running",
                 "pages_done": 1, "pages_total": 100, "error": None},
            ],
        )

        finalised = await reaper.tick()

        assert finalised == 0
        assert row.status == SyncStatus.RUNNING
        portal.report_sync_status.assert_not_awaited()


class TestReaperForceFailsAfter7Days:
    """AC-06.4: 7+ day old still-running rows force-fail with remote_job_stuck."""

    @pytest.mark.asyncio
    async def test_running_over_7d_force_fails(self) -> None:
        old = datetime.now(UTC) - timedelta(days=8)
        row = _row(started_at=old)
        reaper, _, _, portal, _ = _make_reaper(
            candidate_rows=[row],
            status_responses=[
                {"job_id": "abc123", "status": "running",
                 "pages_done": 50, "pages_total": 100, "error": None},
            ],
        )

        finalised = await reaper.tick()

        assert finalised == 1
        assert row.status == SyncStatus.FAILED
        assert row.error_details[0]["error"] == "remote_job_stuck"
        portal.report_sync_status.assert_awaited_once()


class TestReaperUpstreamUnreachable:
    """Knowledge-ingest down -> reaper logs and retries on next tick."""

    @pytest.mark.asyncio
    async def test_connect_error_is_swallowed(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)
        row = _row(started_at=old)
        reaper, _, _, portal, _ = _make_reaper(
            candidate_rows=[row],
            status_responses=[httpx.ConnectError("connection refused")],
        )

        finalised = await reaper.tick()

        assert finalised == 0
        assert row.status == SyncStatus.RUNNING
        portal.report_sync_status.assert_not_awaited()


class TestReaperRaceWithResolver:
    """If the resolver-on-read finalised the row first, the reaper is a no-op."""

    @pytest.mark.asyncio
    async def test_already_terminal_row_is_skipped_in_finalise(self) -> None:
        old = datetime.now(UTC) - timedelta(hours=25)
        # Build a row that LOOKS like the candidate query found it, but
        # by the time the reaper does session.get(...) it's already
        # COMPLETED (resolver got there first).
        candidate = _row(started_at=old)
        registry_row = _row(
            started_at=old,
            status=SyncStatus.COMPLETED,
        )
        registry_row.id = candidate.id  # same row, post-resolver state

        reaper, _, _, portal, registry = _make_reaper(
            candidate_rows=[candidate],
            status_responses=[
                {"job_id": "abc123", "status": "completed",
                 "pages_done": 10, "pages_total": 10, "error": None},
            ],
        )
        # Override the registry: session.get returns the post-resolver row.
        registry[candidate.id] = registry_row

        finalised = await reaper.tick()

        # Reaper observes the registry row, sees status != RUNNING, returns
        # early. The candidate row mock is not mutated, the post-resolver
        # row is left as-is, and no portal callback is emitted.
        assert finalised == 1  # tick still counts the row as 'handled'
        assert registry_row.status == SyncStatus.COMPLETED
        portal.report_sync_status.assert_not_awaited()
