"""Portal-driven scheduler: refresh reconciliation and tenant-scoped sync triggers.

No real Postgres and no portal HTTP: the portal client is an AsyncMock and
``app.core.database.session_maker`` is swapped for a mock session maker
(restored by an autouse fixture), following tests/services/test_sync_run_reaper.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import inspect as sa_inspect

import app.core.database as _db_module
from app.core.enums import SyncStatus
from app.services.portal_client import ScheduledConnector
from app.services.scheduler import REFRESH_JOB_ID, ConnectorScheduler


@pytest.fixture(autouse=True)
def _reset_db_session_maker():
    """Restore app.core.database.session_maker after each test.

    The trigger tests inject a mock session maker so tenant_scoped_session()
    works without a real database engine.
    """
    original = _db_module.session_maker
    yield
    _db_module.session_maker = original


def _scheduled(
    connector_id: uuid.UUID | None = None,
    org_id: str = "org-a",
    schedule: str = "0 3 * * *",
) -> ScheduledConnector:
    return ScheduledConnector(connector_id=connector_id or uuid.uuid4(), org_id=org_id, schedule=schedule)


def _portal_mock(items: list[ScheduledConnector]) -> MagicMock:
    portal = MagicMock()
    portal.list_scheduled_connectors = AsyncMock(return_value=items)
    return portal


@contextlib.asynccontextmanager
async def _started(portal: MagicMock) -> AsyncIterator[ConnectorScheduler]:
    """Real ConnectorScheduler, started on the test loop, always shut down."""
    scheduler = ConnectorScheduler()
    await scheduler.start(portal, AsyncMock())
    try:
        yield scheduler
    finally:
        await scheduler.shutdown()


def _make_session(existing_running_run: Any | None = None) -> tuple[MagicMock, uuid.UUID]:
    """Mock AsyncSession shaped for tenant_scoped_session() (see _pin_and_reset_connection).

    ``session.execute`` answers the RUNNING-run guard with
    ``existing_running_run``; ``session.refresh`` materialises the ORM
    default id the same way a real flush would.

    Leaving the block expires what it loaded, like the real thing. Without
    that, this mock answers ``sync_run.id`` forever and the test cannot see
    the one thing that matters here: whether the caller read the id while
    the session was still open. Every scheduled sync on 2026-09-11 died on
    exactly that access, and this file was green throughout.
    """
    sync_run_id = uuid.uuid4()
    added: list[Any] = []
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)

    async def _rollback() -> None:
        # tenant_scoped_session() always rolls back on exit
        # (_reset_tenant_context), and a rollback expires the identity map.
        # Reproduced with SQLAlchemy's own expiry so an access afterwards
        # takes the real code path and raises DetachedInstanceError.
        for obj in added:
            state = sa_inspect(obj)
            state._expire(state.dict, set())

    sess.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(first=lambda: existing_running_run))
    sess.execute = AsyncMock(return_value=result)

    def _add(obj: Any) -> None:
        added.append(obj)
        # Snapshot now. After close the instance is expired, and reading it
        # then is the very habit that let the bug through.
        sess.added_rows.append({"connector_id": obj.connector_id, "org_id": obj.org_id, "status": obj.status})

    sess.added_rows = []
    sess.add = MagicMock(side_effect=_add)
    sess.commit = AsyncMock()

    def _refresh(obj: Any) -> None:
        obj.id = sync_run_id

    sess.refresh = AsyncMock(side_effect=_refresh)
    sess.connection = AsyncMock()
    sess.rollback = AsyncMock(side_effect=_rollback)
    return sess, sync_run_id


class TestRefreshReconciliation:
    """refresh() registers one cron job per portal item and reconciles on re-fetch."""

    @pytest.mark.asyncio
    async def test_refresh_registers_jobs_from_portal(self) -> None:
        a, b = _scheduled(), _scheduled()
        async with _started(_portal_mock([a, b])) as scheduler:
            job_a = scheduler._scheduler.get_job(str(a.connector_id))
            job_b = scheduler._scheduler.get_job(str(b.connector_id))
            assert job_a is not None and isinstance(job_a.trigger, CronTrigger)
            assert job_b is not None and isinstance(job_b.trigger, CronTrigger)
            assert scheduler._scheduler.get_job(REFRESH_JOB_ID) is not None

    @pytest.mark.asyncio
    async def test_refresh_removes_jobs_no_longer_scheduled(self) -> None:
        a, b = _scheduled(), _scheduled()
        portal = _portal_mock([a, b])
        async with _started(portal) as scheduler:
            assert scheduler._scheduler.get_job(str(b.connector_id)) is not None

            portal.list_scheduled_connectors.return_value = [a]
            await scheduler.refresh()

            assert scheduler._scheduler.get_job(str(b.connector_id)) is None
            assert scheduler._scheduler.get_job(str(a.connector_id)) is not None
            # The refresh job itself must survive reconciliation.
            assert scheduler._scheduler.get_job(REFRESH_JOB_ID) is not None

    @pytest.mark.asyncio
    async def test_refresh_keeps_jobs_when_portal_fails(self, caplog: pytest.LogCaptureFixture) -> None:
        a = _scheduled()
        portal = _portal_mock([a])
        with caplog.at_level(logging.ERROR):
            async with _started(portal) as scheduler:
                portal.list_scheduled_connectors.side_effect = httpx.HTTPError("portal unavailable")
                await scheduler.refresh()  # MUST NOT raise

                assert scheduler._scheduler.get_job(str(a.connector_id)) is not None
                assert scheduler._scheduler.get_job(REFRESH_JOB_ID) is not None
        assert any("scheduled connectors" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_refresh_skips_invalid_cron_and_schedules_the_rest(self, caplog: pytest.LogCaptureFixture) -> None:
        bad = _scheduled(schedule="not a cron")
        good = _scheduled()
        with caplog.at_level(logging.ERROR):
            async with _started(_portal_mock([bad, good])) as scheduler:
                assert scheduler._scheduler.get_job(str(bad.connector_id)) is None
                assert scheduler._scheduler.get_job(str(good.connector_id)) is not None
        assert any(str(bad.connector_id) in record.getMessage() for record in caplog.records)


class TestTriggerSync:
    """_trigger_sync inserts a tenant-scoped SyncRun and defers to the sync engine."""

    @pytest.mark.asyncio
    async def test_trigger_sync_creates_run_with_org_id_and_calls_callback(self) -> None:
        scheduler = ConnectorScheduler()
        callback = AsyncMock()
        scheduler._sync_callback = callback
        sess, sync_run_id = _make_session()
        _db_module.session_maker = MagicMock(return_value=sess)

        connector_id = uuid.uuid4()
        await scheduler._trigger_sync(connector_id, "org-zitadel-1")
        await asyncio.sleep(0)  # let the create_task'd callback run

        assert sess.added_rows == [
            {
                "connector_id": connector_id,
                "org_id": "org-zitadel-1",
                "status": SyncStatus.RUNNING,
            }
        ]
        sess.commit.assert_awaited_once()
        # The id must have been read while the session was open. Passing it
        # on is the whole point: the callback is what actually syncs.
        callback.assert_awaited_once_with(connector_id, sync_run_id)

    @pytest.mark.asyncio
    async def test_trigger_sync_skips_when_run_already_running(self, caplog: pytest.LogCaptureFixture) -> None:
        scheduler = ConnectorScheduler()
        callback = AsyncMock()
        scheduler._sync_callback = callback
        sess, _ = _make_session(existing_running_run=MagicMock())
        _db_module.session_maker = MagicMock(return_value=sess)

        connector_id = uuid.uuid4()
        with caplog.at_level(logging.WARNING):
            await scheduler._trigger_sync(connector_id, "org-zitadel-1")

        sess.add.assert_not_called()
        sess.commit.assert_not_awaited()
        callback.assert_not_called()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(str(connector_id) in r.getMessage() for r in warnings)
