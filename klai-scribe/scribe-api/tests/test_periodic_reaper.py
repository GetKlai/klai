"""The reaper runs while the service is up, not only at startup.

A row the queue can no longer run (attempts used up, audio gone) used to stay
`processing` until the next deploy restarted the process.

The loop is driven by a fake `asyncio.sleep` that counts its calls and cancels
the loop on the last one, so the tests do not depend on wall-clock timing
(a 10 ms interval gave one iteration instead of three on a loaded CI runner).
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


def _sleep_that_cancels_after(ticks: int, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) > ticks:
            raise asyncio.CancelledError

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    return delays


async def test_reaper_runs_on_every_interval(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    reap = AsyncMock(return_value=1)
    monkeypatch.setattr(main, "reap_stranded", reap)
    delays = _sleep_that_cancels_after(3, monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        await main._reap_periodically()

    assert reap.await_count == 3
    assert delays[:3] == [main._REAP_INTERVAL_S] * 3
    assert reap.await_args.kwargs["timeout_min"] == main.settings.scribe_stranded_timeout_min


async def test_a_failing_reap_does_not_stop_the_loop(
    session: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    reap = AsyncMock(side_effect=[RuntimeError("db down"), 0, 0])
    monkeypatch.setattr(main, "reap_stranded", reap)
    _sleep_that_cancels_after(3, monkeypatch)

    with pytest.raises(asyncio.CancelledError):
        await main._reap_periodically()

    assert reap.await_count == 3
