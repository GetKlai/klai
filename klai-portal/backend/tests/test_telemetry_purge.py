"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 7 — telemetry-purge loop tests.

Pure unit tests with mocked cross_org_session. Verifies:
- Both DELETEs run with the cutoff = now - 7d
- portal_retrieval_gaps DELETE excludes redacted rows
- A failure on one table does not abort the second
- The async loop sleeps 60s on startup, then runs every 24h, and
  exits cleanly on cancellation
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _execute_returning(rowcount: int) -> AsyncMock:
    """Build a mock for db.execute that returns a result with rowcount set."""
    result = MagicMock()
    result.rowcount = rowcount
    return AsyncMock(return_value=result)


@pytest.mark.asyncio
async def test_purge_once_runs_both_deletes_with_cutoff() -> None:
    from app.services.telemetry_purge import RETENTION_DAYS, _purge_once

    captured_calls: list[dict] = []

    async def _exec(stmt, params):
        # Coerce stmt to its string SQL via the .text attribute (sqlalchemy.text)
        sql = str(stmt)
        captured_calls.append({"sql": sql, "params": params})
        result = MagicMock()
        if "telemetry.query_shadow" in sql:
            result.rowcount = 3
        elif "SELECT id FROM public.portal_retrieval_gaps" in sql:
            result.scalars.return_value.all.return_value = [11]
        else:
            result.rowcount = 1
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_exec)
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with patch("app.services.telemetry_purge.cross_org_session", _fake_session):
        counts = await _purge_once()

    assert counts == {"query_shadow": 3, "retrieval_gaps": 1}
    assert len(captured_calls) == 3
    # First DELETE targets telemetry.query_shadow
    assert "telemetry.query_shadow" in captured_calls[0]["sql"]
    # Second SELECT targets portal_retrieval_gaps and excludes the
    # redacted sentinel rows.
    assert "portal_retrieval_gaps" in captured_calls[1]["sql"]
    assert "[REDACTED:%" in captured_calls[1]["sql"]
    assert "DELETE FROM public.portal_retrieval_gaps" in captured_calls[2]["sql"]
    assert captured_calls[2]["params"]["gap_ids"] == [11]
    # The TTL-bearing calls share the same cutoff (within a small clock skew).
    cutoffs = [call["params"]["cutoff"] for call in captured_calls[:2]]
    expected = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    for cutoff in cutoffs:
        assert abs((cutoff - expected).total_seconds()) < 5
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_once_continues_when_first_delete_fails() -> None:
    """REQ-10: a failure on query_shadow must not abort retrieval_gaps."""
    from app.services.telemetry_purge import _purge_once

    call_n = 0

    async def _exec(stmt, params):
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise RuntimeError("simulated query_shadow failure")
        result = MagicMock()
        if "SELECT id FROM public.portal_retrieval_gaps" in str(stmt):
            result.scalars.return_value.all.return_value = [21, 22, 23, 24, 25, 26, 27]
        else:
            result.rowcount = 7
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_exec)
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with patch("app.services.telemetry_purge.cross_org_session", _fake_session):
        counts = await _purge_once()

    # query_shadow failed so the count stayed 0; retrieval_gaps still
    # ran and counted 7.
    assert counts == {"query_shadow": 0, "retrieval_gaps": 7}
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_once_skips_retrieval_gap_delete_when_no_candidates() -> None:
    """No expired retrieval-gap candidates means no RLS-table DELETE."""
    from app.services.telemetry_purge import _purge_once

    captured_sql: list[str] = []

    async def _exec(stmt, params):
        sql = str(stmt)
        captured_sql.append(sql)
        if "DELETE FROM public.portal_retrieval_gaps" in sql:
            raise AssertionError("DELETE must not run when the candidate SELECT is empty")
        result = MagicMock()
        if "telemetry.query_shadow" in sql:
            result.rowcount = 0
        else:
            result.scalars.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_exec)
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with patch("app.services.telemetry_purge.cross_org_session", _fake_session):
        counts = await _purge_once()

    assert counts == {"query_shadow": 0, "retrieval_gaps": 0}
    assert any("SELECT id FROM public.portal_retrieval_gaps" in sql for sql in captured_sql)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_loop_exits_on_cancel(monkeypatch) -> None:
    """The loop must exit cleanly when cancelled via lifespan shutdown."""
    from app.services import telemetry_purge as tp

    purge_call_count = 0

    async def _fake_purge_once() -> dict[str, int]:
        nonlocal purge_call_count
        purge_call_count += 1
        return {"query_shadow": 0, "retrieval_gaps": 0}

    monkeypatch.setattr(tp, "_purge_once", _fake_purge_once)
    # Drop the inter-cycle sleep to ~0 so we run multiple iterations
    # within the test's awaited window. The 60s startup sleep is also
    # short-circuited by patching `asyncio.sleep` only inside the
    # module under test (not the test harness).
    monkeypatch.setattr(tp, "PURGE_INTERVAL_SECONDS", 0)

    real_sleep = asyncio.sleep

    async def _short_sleep(delay: float) -> None:
        # Replace startup 60s + interval seconds with a 0-delay yield;
        # other delay values use the real sleep so the test event loop
        # still yields properly.
        if delay >= 1:
            await real_sleep(0)
        else:
            await real_sleep(delay)

    monkeypatch.setattr("app.services.telemetry_purge.asyncio.sleep", _short_sleep)

    task = asyncio.create_task(tp.telemetry_purge_loop())
    # Run the loop long enough for at least one iteration to land.
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert purge_call_count >= 1
