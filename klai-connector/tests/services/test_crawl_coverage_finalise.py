"""Resolver and reaper carry the crawl coverage and finalise on the remote status alone.

knowledge-ingest decides whether a crawl that left addresses for later is
``completed``; the connector only stores the coverage it reports.
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
COMPLETED = {"job_id": "abc123", "status": "completed", "pages_done": 80, "pages_total": 80, "coverage": COVERAGE}
FAILED = {
    "job_id": "abc123",
    "status": "failed_partial",
    "pages_done": 80,
    "pages_total": 80,
    "error": "auth_wall",
    "coverage": COVERAGE,
}


@pytest.mark.asyncio
async def test_resolver_keeps_the_coverage_of_a_completed_crawl() -> None:
    resolver, _, _, _ = _make_resolver(status_responses=[COMPLETED])
    row = _resolver_row()
    _register(resolver, row)

    snapshot = await resolver.resolve(row)

    assert row.status == SyncStatus.COMPLETED
    assert row.cursor_state["remote_status"] == "completed"
    assert row.cursor_state["coverage"] == COVERAGE
    assert snapshot.coverage == COVERAGE


@pytest.mark.asyncio
async def test_reaper_keeps_the_coverage_of_a_completed_crawl() -> None:
    row = _reaper_row(started_at=datetime.now(UTC) - timedelta(hours=25))
    reaper, _, _, _, _ = _make_reaper(candidate_rows=[row], status_responses=[COMPLETED])

    await reaper.tick()

    assert row.status == SyncStatus.COMPLETED
    assert row.cursor_state["remote_status"] == "completed"
    assert row.cursor_state["coverage"] == COVERAGE


@pytest.mark.asyncio
async def test_a_failed_crawl_stays_failed_whatever_its_coverage_says() -> None:
    resolver, _, _, _ = _make_resolver(status_responses=[FAILED])
    row = _resolver_row()
    _register(resolver, row)
    await resolver.resolve(row)

    reaper_row = _reaper_row(started_at=datetime.now(UTC) - timedelta(hours=25))
    reaper, _, _, _, _ = _make_reaper(candidate_rows=[reaper_row], status_responses=[FAILED])
    await reaper.tick()

    assert row.status == SyncStatus.FAILED
    assert reaper_row.status == SyncStatus.FAILED
