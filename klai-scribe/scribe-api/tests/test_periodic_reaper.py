"""The reaper runs while the service is up, not only at startup.

A row the queue can no longer run (attempts used up, audio gone) used to stay
`processing` until the next deploy restarted the process.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import main


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "AsyncSessionLocal", lambda: session)
    return session


async def test_reaper_keeps_running_on_its_interval(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    reap = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "reap_stranded", reap)
    monkeypatch.setattr(main, "_REAP_INTERVAL_S", 0.01)

    task = asyncio.create_task(main._reap_periodically())
    await asyncio.sleep(0.08)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert reap.await_count >= 3
    assert reap.await_args.kwargs["timeout_min"] == main.settings.scribe_stranded_timeout_min


async def test_a_failing_reap_does_not_stop_the_loop(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    reap = AsyncMock(side_effect=[RuntimeError("db down"), 0, 0])
    monkeypatch.setattr(main, "reap_stranded", reap)
    monkeypatch.setattr(main, "_REAP_INTERVAL_S", 0.01)

    task = asyncio.create_task(main._reap_periodically())
    await asyncio.sleep(0.06)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert reap.await_count >= 2
