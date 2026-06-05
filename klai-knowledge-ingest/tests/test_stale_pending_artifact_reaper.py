from __future__ import annotations

import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class _RetryStrategy:
    def __init__(self, *, max_attempts: int):
        self.max_attempts = max_attempts


def _install_procrastinate_stub() -> None:
    if "procrastinate" in sys.modules:
        return
    proc_mod = types.ModuleType("procrastinate")
    proc_mod.RetryStrategy = _RetryStrategy
    sys.modules["procrastinate"] = proc_mod


_install_procrastinate_stub()


class _FakeApp:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, dict]] = []
        self.periodics: list[tuple[object, dict]] = []

    def task(self, **kwargs):
        def _decorator(fn):
            self.tasks.append((fn, kwargs))
            return fn

        return _decorator

    def periodic(self, **kwargs):
        def _decorator(fn):
            self.periodics.append((fn, kwargs))
            return fn

        return _decorator


@pytest.mark.asyncio
async def test_reap_stale_pending_artifacts_uses_cross_org_admin_connection():
    conn = MagicMock()
    expected = [
        {
            "artifact_id": "11111111-2222-3333-4444-555555555555",
            "org_id": "org1",
            "kb_slug": "oracle",
            "path": "team.md",
            "created_at": 1700000000,
        }
    ]

    @asynccontextmanager
    async def _ctx():
        yield conn

    with (
        patch("knowledge_ingest.stale_pending_artifact_reaper.time.time", return_value=1700003600),
        patch(
            "knowledge_ingest.stale_pending_artifact_reaper.cross_org_admin_connection",
            return_value=_ctx(),
        ),
        patch(
            "knowledge_ingest.stale_pending_artifact_reaper.pg_store.mark_stale_pending_artifacts_failed",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_mark,
    ):
        from knowledge_ingest.stale_pending_artifact_reaper import reap_stale_pending_artifacts

        result = await reap_stale_pending_artifacts(stale_after_seconds=1800, limit=25)

    assert result == expected
    mock_mark.assert_awaited_once_with(conn, cutoff_created_at=1700001800, limit=25)


def test_register_stale_pending_artifact_reaper_registers_periodic_io_task():
    from knowledge_ingest.stale_pending_artifact_reaper import (
        register_stale_pending_artifact_reaper,
    )

    app = _FakeApp()
    register_stale_pending_artifact_reaper(app)

    assert len(app.periodics) == 1
    assert app.periodics[0][1] == {
        "cron": "*/15 * * * *",
        "periodic_id": "stale-pending-artifact-reaper",
    }
    assert len(app.tasks) == 1
    task_kwargs = app.tasks[0][1]
    assert task_kwargs["name"] == (
        "knowledge_ingest.stale_pending_artifact_reaper."
        "reap_stale_pending_artifacts_periodic"
    )
    assert task_kwargs["queue"] == "ingest-kb"
    assert task_kwargs["queueing_lock"] == "stale-pending-artifact-reaper"
    assert task_kwargs["retry"] is not None
    assert hasattr(app, "reap_stale_pending_artifacts_periodic")


@pytest.mark.asyncio
async def test_periodic_task_returns_failed_count():
    from knowledge_ingest.stale_pending_artifact_reaper import (
        register_stale_pending_artifact_reaper,
    )

    app = _FakeApp()
    register_stale_pending_artifact_reaper(app)

    with patch(
        "knowledge_ingest.stale_pending_artifact_reaper.reap_stale_pending_artifacts",
        new_callable=AsyncMock,
        return_value=[{"artifact_id": "a1"}, {"artifact_id": "a2"}],
    ):
        result = await app.reap_stale_pending_artifacts_periodic(timestamp=1700000000)

    assert result == {"failed": 2}
