"""Durable crawl checkpoint and execution-fencing contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from knowledge_ingest.crawl_checkpoint import (
    CrawlExecutionSuperseded,
    PostgresCrawlCheckpoint,
    claim_crawl_execution,
    finish_crawl_execution,
    guard_crawl_execution,
)


class _Connection:
    def __init__(self, *, generation: int = 41, cancelled: bool = False) -> None:
        self.generation = generation
        self.cancelled = cancelled
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.executemany = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "UPDATE 1"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FOR UPDATE" in query:
            return {
                "execution_generation": self.generation,
                "cancel_requested": self.cancelled,
                "status": "running",
                "runtime_checkpoint": {},
            }
        return None


@pytest.mark.asyncio
async def test_checkpoint_save_is_fenced_and_splits_frontier_rows() -> None:
    conn = _Connection()
    store = PostgresCrawlCheckpoint(
        conn, job_id="job-1", org_id="org-1", scope="primary", generation=41
    )

    await store.save(
        {
            "version": 1,
            "start_url": "https://example.com",
            "complete": False,
            "ledger": [
                {
                    "url": "https://example.com",
                    "canonical_url": "https://example.com/",
                    "depth": 0,
                    "discovered_from": None,
                    "source_kind": "start",
                    "priority": 0,
                    "order": 1,
                    "status": "fetched",
                    "reason_code": "success",
                }
            ],
            "results": [
                {
                    "url": "https://example.com/home",
                    "requested_url": "https://example.com",
                }
            ],
            "outcomes": [{"url": "https://example.com", "reason_code": "success"}],
            "fetched_count": 1,
        }
    )

    conn.executemany.assert_awaited_once()
    sql, rows = conn.executemany.await_args.args
    assert "crawl_job_frontier" in sql
    assert rows[0][0:4] == ("job-1", "org-1", "primary", "https://example.com/")
    assert '"https://example.com/home"' in rows[0][12]
    parent_update = next(query for query, _args in conn.executed if "checkpoint_sequence" in query)
    assert "execution_generation" in parent_update


@pytest.mark.asyncio
async def test_checkpoint_rejects_a_superseded_execution() -> None:
    conn = _Connection(generation=42)
    store = PostgresCrawlCheckpoint(
        conn, job_id="job-1", org_id="org-1", scope="primary", generation=41
    )

    with pytest.raises(CrawlExecutionSuperseded):
        await store.ensure_active()


@pytest.mark.asyncio
async def test_checkpoint_allows_current_execution_to_flush_after_cancel() -> None:
    conn = _Connection(cancelled=True)
    store = PostgresCrawlCheckpoint(
        conn, job_id="job-1", org_id="org-1", scope="primary", generation=41
    )

    await store.ensure_active()


@pytest.mark.asyncio
async def test_claim_sets_a_new_generation_before_network_work(monkeypatch) -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    monkeypatch.setattr("knowledge_ingest.crawl_checkpoint.time.time_ns", lambda: 123456)

    generation = await claim_crawl_execution(conn, "job-1")

    assert generation == 123456
    queries = [call.args[0] for call in conn.execute.await_args_list]
    assert "pg_advisory_lock" in queries[0]
    assert "execution_generation=$1" in queries[1]
    assert "cancel_requested = false" in queries[1]
    assert "pg_advisory_unlock" in queries[-1]


@pytest.mark.asyncio
async def test_execution_guard_unlocks_when_generation_was_superseded() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=["SELECT 1", "UPDATE 0", "SELECT 1"])

    with pytest.raises(CrawlExecutionSuperseded):
        async with guard_crawl_execution(conn, "job-1", 41):
            pytest.fail("superseded execution entered guarded side effects")

    queries = [call.args[0] for call in conn.execute.await_args_list]
    assert "pg_advisory_lock" in queries[0]
    assert "pg_advisory_unlock" in queries[-1]


@pytest.mark.asyncio
async def test_finish_is_atomic_and_generation_fenced() -> None:
    conn = _Connection()

    await finish_crawl_execution(conn, "job-1", 41, status="completed")

    query, args = conn.executed[-1]
    assert "execution_generation=$5" in query
    assert "status='running'" in query
    assert args == ("completed", None, None, "job-1", 41)
