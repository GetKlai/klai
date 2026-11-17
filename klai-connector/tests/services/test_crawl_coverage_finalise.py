"""Resolver and reaper finalise a crawl that left addresses for later as a succeeded sync.

Both finalisers must apply the same decision (``remote_crawl_succeeded``): the
reaper ends most nightly runs, the resolver ends those someone opens live.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import SyncStatus
from tests.services.test_sync_run_reaper import _make_reaper
from tests.services.test_sync_run_reaper import _reset_db_session_maker as _reset_reaper_db  # noqa: F401
from tests.services.test_sync_run_reaper import _row as _reaper_row
from tests.services.test_sync_run_resolver import _make_resolver, _register
from tests.services.test_sync_run_resolver import _reset_db_session_maker as _reset_resolver_db  # noqa: F401
from tests.services.test_sync_run_resolver import _row as _resolver_row

COVERAGE = {
    "pages_read": 80,
    "addresses_gone": 3,
    "addresses_waiting": 120,
    "addresses_known": 203,
    "max_pages": 80,
    "stopped_by": "page_limit",
}
LIVE = {
    "job_id": "abc123",
    "status": "failed",
    "pages_done": 80,
    "pages_total": 80,
    "error": "crawl_budget_exhausted",
    "coverage": COVERAGE,
}


@pytest.mark.asyncio
async def test_resolver_completes_a_budget_stop_and_keeps_the_coverage() -> None:
    resolver, _, _, portal = _make_resolver(status_responses=[LIVE])
    row = _resolver_row()
    _register(resolver, row)

    snapshot = await resolver.resolve(row)

    assert row.status == SyncStatus.COMPLETED
    assert row.error_details is None
    assert row.documents_failed == 0
    assert row.cursor_state["coverage"] == COVERAGE
    assert snapshot.coverage == COVERAGE
    assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.COMPLETED


@pytest.mark.asyncio
async def test_reaper_completes_a_budget_stop_and_keeps_the_coverage() -> None:
    row = _reaper_row(started_at=datetime.now(UTC) - timedelta(hours=25))
    reaper, _, _, portal, _ = _make_reaper(candidate_rows=[row], status_responses=[LIVE])

    await reaper.tick()

    assert row.status == SyncStatus.COMPLETED
    assert row.error_details is None
    assert row.cursor_state["coverage"] == COVERAGE
    assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.COMPLETED
