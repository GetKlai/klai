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
        if "SET execution_generation=execution_generation" in query and args[1] != self.generation:
            return "UPDATE 0"
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


def _connection_factory(conn: Any):
    @asynccontextmanager
    async def _acquire():
        yield conn

    return _acquire


@pytest.mark.asyncio
async def test_checkpoint_save_is_fenced_and_splits_frontier_rows() -> None:
    conn = _Connection()
    store = PostgresCrawlCheckpoint(
        _connection_factory(conn),
        job_id="job-1",
        org_id="org-1",
        scope="primary",
        generation=41,
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
            "results_delta": [
                {
                    "url": "https://example.com/home",
                    "requested_url": "https://example.com",
                }
            ],
            "outcomes_delta": [{"url": "https://example.com", "reason_code": "success"}],
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
        _connection_factory(conn),
        job_id="job-1",
        org_id="org-1",
        scope="primary",
        generation=41,
    )

    with pytest.raises(CrawlExecutionSuperseded):
        await store.ensure_active()


@pytest.mark.asyncio
async def test_checkpoint_allows_current_execution_to_flush_after_cancel() -> None:
    conn = _Connection(cancelled=True)
    store = PostgresCrawlCheckpoint(
        _connection_factory(conn),
        job_id="job-1",
        org_id="org-1",
        scope="primary",
        generation=41,
    )

    await store.ensure_active()


@pytest.mark.asyncio
async def test_claim_sets_a_new_generation_before_network_work(monkeypatch) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetchrow = AsyncMock(return_value={"execution_generation": 123456})
    conn.execute = AsyncMock(return_value="SELECT 1")

    generation = await claim_crawl_execution(conn, "job-1")

    assert generation == 123456
    lock_query = conn.fetchval.await_args.args[0]
    assert "pg_try_advisory_lock" in lock_query
    claim_query = conn.fetchrow.await_args.args[0]
    assert "execution_generation=execution_generation + 1" in claim_query
    assert "RETURNING execution_generation" in claim_query
    assert "cancel_requested = false" in claim_query
    assert "pg_advisory_unlock" in conn.execute.await_args.args[0]


@pytest.mark.asyncio
async def test_claim_fails_fast_when_execution_lock_is_busy() -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.fetchrow = AsyncMock()
    conn.execute = AsyncMock()

    with pytest.raises(RuntimeError, match="busy"):
        await claim_crawl_execution(conn, "job-1")

    conn.fetchrow.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_execution_guard_unlocks_when_generation_was_superseded() -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.execute = AsyncMock(side_effect=["UPDATE 0", "SELECT 1"])

    with pytest.raises(CrawlExecutionSuperseded):
        async with guard_crawl_execution(conn, "job-1", 41):
            pytest.fail("superseded execution entered guarded side effects")

    assert "pg_try_advisory_lock" in conn.fetchval.await_args.args[0]
    assert "pg_advisory_unlock" in conn.execute.await_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_execution_guard_fails_fast_when_lock_is_busy() -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)
    conn.execute = AsyncMock()

    with pytest.raises(RuntimeError, match="busy"):
        async with guard_crawl_execution(conn, "job-1", 41):
            pytest.fail("busy execution entered guarded side effects")

    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_checkpoint_writes_only_rows_changed_since_previous_batch() -> None:
    conn = _Connection()
    store = PostgresCrawlCheckpoint(
        _connection_factory(conn),
        job_id="job-1",
        org_id="org-1",
        scope="primary",
        generation=41,
    )
    first_row = {
        "url": "https://example.com/a",
        "canonical_url": "https://example.com/a",
        "depth": 0,
        "discovered_from": None,
        "source_kind": "start",
        "priority": 0,
        "order": 1,
        "status": "fetched",
        "reason_code": "success",
    }
    second_row = {
        "url": "https://example.com/b",
        "canonical_url": "https://example.com/b",
        "depth": 1,
        "discovered_from": "https://example.com/a",
        "source_kind": "page_link",
        "priority": 4,
        "order": 2,
        "status": "fetched",
        "reason_code": "success",
    }

    await store.save(
        {
            "version": 1,
            "ledger": [first_row],
            "results_delta": [{"url": first_row["url"]}],
            "outcomes_delta": [{"url": first_row["url"], "reason_code": "success"}],
        }
    )
    await store.save(
        {
            "version": 1,
            "ledger": [first_row, second_row],
            "results_delta": [{"url": second_row["url"]}],
            "outcomes_delta": [{"url": second_row["url"], "reason_code": "success"}],
        }
    )

    assert conn.executemany.await_count == 2
    _sql, second_batch = conn.executemany.await_args_list[1].args
    assert len(second_batch) == 1
    assert second_batch[0][3] == "https://example.com/b"


@pytest.mark.asyncio
async def test_checkpoint_load_reads_parent_and_frontier_in_one_transaction() -> None:
    class _LoadConnection:
        def __init__(self) -> None:
            self.in_transaction = False

        @asynccontextmanager
        async def transaction(self):
            self.in_transaction = True
            try:
                yield
            finally:
                self.in_transaction = False

        async def fetchrow(self, query: str, *_args: Any) -> dict[str, Any]:
            assert self.in_transaction
            if "FOR UPDATE" in query:
                return {
                    "execution_generation": 41,
                    "status": "running",
                    "runtime_checkpoint": {"primary": {"version": 1}},
                }
            return {"runtime_checkpoint": {"primary": {"version": 1}}}

        async def fetch(self, _query: str, *_args: Any) -> list[dict[str, Any]]:
            assert self.in_transaction
            return []

    conn = _LoadConnection()
    store = PostgresCrawlCheckpoint(
        _connection_factory(conn),
        job_id="job-1",
        org_id="org-1",
        scope="primary",
        generation=41,
    )

    snapshot = await store.load()

    assert snapshot == {"version": 1, "ledger": [], "results": [], "outcomes": []}
    assert conn.in_transaction is False


@pytest.mark.asyncio
async def test_finish_is_atomic_and_generation_fenced() -> None:
    conn = _Connection()

    await finish_crawl_execution(conn, "job-1", 41, status="completed")

    update_query, args = conn.executed[-2]
    delete_query, delete_args = conn.executed[-1]
    assert "execution_generation=$5" in update_query
    assert "status='running'" in update_query
    assert "runtime_checkpoint=NULL" in update_query
    assert args == ("completed", None, None, "job-1", 41)
    assert "DELETE FROM knowledge.crawl_job_frontier" in delete_query
    assert delete_args == ("job-1",)
