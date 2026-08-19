"""SPEC-CRAWL-001 AC-14 — conflict-safe domain-rate persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from knowledge_ingest.domain_rate_limit_control import DomainRateLimitState
from knowledge_ingest.domain_selectors import (
    DomainRateLimitWriteKind,
    save_domain_rate_limit_state,
)

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
INITIAL = DomainRateLimitState(
    rate_limit=0.4,
    clean_streak=3,
    last_congestion_at=NOW - timedelta(days=2),
)
LOWERED = DomainRateLimitState(rate_limit=0.2, clean_streak=0, last_congestion_at=NOW)
RECOVERED = DomainRateLimitState(
    rate_limit=0.9,
    clean_streak=4,
    last_congestion_at=INITIAL.last_congestion_at,
)


class _AtomicStateConnection:
    """Model the one-statement PostgreSQL boundary used by the repository."""

    def __init__(self, state: DomainRateLimitState) -> None:
        self.state = state
        self.lock = asyncio.Lock()
        self.queries: list[str] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.queries.append(query)
        assert "ON CONFLICT (domain, org_id) DO UPDATE" in query
        assert "LEAST" in query
        assert "IS NOT DISTINCT FROM" in query

        proposed = DomainRateLimitState(args[2], args[3], args[4])
        expected = DomainRateLimitState(args[5], args[6], args[7])
        default_rate = args[8]
        is_congestion = args[9]

        async with self.lock:
            if is_congestion:
                current_rate = self.state.rate_limit or default_rate
                self.state = DomainRateLimitState(
                    rate_limit=min(current_rate, proposed.rate_limit),
                    clean_streak=0,
                    last_congestion_at=max(
                        timestamp
                        for timestamp in (
                            self.state.last_congestion_at,
                            proposed.last_congestion_at,
                        )
                        if timestamp is not None
                    ),
                )
                return {"rate_limit": self.state.rate_limit}
            if self.state == expected:
                self.state = proposed
                return {"rate_limit": self.state.rate_limit}
            return None


async def _write(
    conn: _AtomicStateConnection,
    proposed: DomainRateLimitState,
    kind: DomainRateLimitWriteKind,
) -> bool:
    return await save_domain_rate_limit_state(
        conn,  # type: ignore[arg-type]
        "example.com",
        "org-1",
        expected_state=INITIAL,
        state=proposed,
        kind=kind,
        default_rate_limit=2.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("congestion_first", [True, False])
async def test_crossing_recovery_and_lowering_finish_at_the_lower_proposal(
    congestion_first: bool,
) -> None:
    conn = _AtomicStateConnection(INITIAL)
    first = (LOWERED, DomainRateLimitWriteKind.CONGESTION)
    second = (RECOVERED, DomainRateLimitWriteKind.RECOVERY)
    if not congestion_first:
        first, second = second, first

    first_applied = await _write(conn, *first)
    second_applied = await _write(conn, *second)

    assert first_applied is True
    assert second_applied is (not congestion_first)
    assert conn.state.rate_limit == 0.2
    assert conn.state.clean_streak == 0
    assert conn.state.last_congestion_at == NOW


@pytest.mark.asyncio
async def test_conflicting_decay_cannot_erase_newer_congestion() -> None:
    conn = _AtomicStateConnection(INITIAL)
    decayed = DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)

    assert await _write(conn, LOWERED, DomainRateLimitWriteKind.CONGESTION) is True
    assert await _write(conn, decayed, DomainRateLimitWriteKind.DECAY) is False
    assert conn.state == LOWERED
