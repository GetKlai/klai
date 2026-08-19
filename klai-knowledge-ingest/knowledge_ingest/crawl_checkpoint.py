"""Postgres-backed crawl checkpoints with execution-generation fencing."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg


class CrawlExecutionSuperseded(RuntimeError):
    """A newer worker attempt owns this crawl job."""


class CrawlExecutionCancelled(RuntimeError):
    """The operator cancelled this crawl job."""


async def claim_crawl_execution(conn: asyncpg.Connection, job_id: str) -> int:
    """Fence older attempts before this attempt performs network work."""
    generation = time.time_ns()
    await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", job_id)
    try:
        command = await conn.execute(
            """
            UPDATE knowledge.crawl_jobs
            SET execution_generation=$1,
                recovery_count=recovery_count + CASE WHEN status='running' THEN 1 ELSE 0 END,
                status='running',
                updated_at=extract(epoch FROM now())::bigint
            WHERE id=$2
              AND status IN ('pending', 'running')
              AND cancel_requested = false
              AND execution_generation < $1
            """,
            generation,
            job_id,
        )
        if command == "UPDATE 0":
            row = await conn.fetchrow(
                "SELECT status, cancel_requested FROM knowledge.crawl_jobs WHERE id=$1",
                job_id,
            )
            if row and row["cancel_requested"]:
                raise CrawlExecutionCancelled(job_id)
            raise CrawlExecutionSuperseded(job_id)
        return generation
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", job_id)


async def ensure_crawl_execution_active(
    conn: asyncpg.Connection, job_id: str, generation: int
) -> None:
    """Fence adapter-side effects that happen after the fetch checkpoint.

    Cancellation is deliberately not part of this fence. ``crawl_site`` polls
    the durable cancellation flag between fetch batches and returns the pages
    it already fetched. The adapter must remain the owner long enough to ingest
    those pages and publish the cancelled terminal state.
    """
    command = await conn.execute(
        """
        UPDATE knowledge.crawl_jobs
        SET execution_generation=execution_generation
        WHERE id=$1 AND execution_generation=$2
          AND status='running'
        """,
        job_id,
        generation,
    )
    if command == "UPDATE 0":
        raise CrawlExecutionSuperseded(job_id)


@asynccontextmanager
async def guard_crawl_execution(
    conn: asyncpg.Connection, job_id: str, generation: int
) -> AsyncIterator[None]:
    """Serialize one side-effect block with recovery without a long DB transaction.

    The session-level advisory lock is released automatically if a hard-killed
    worker loses its connection. ``claim_crawl_execution`` takes the same lock,
    so it cannot install a newer generation halfway through the guarded block.
    """
    await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", job_id)
    try:
        await ensure_crawl_execution_active(conn, job_id, generation)
        yield
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", job_id)


async def finish_crawl_execution(
    conn: asyncpg.Connection,
    job_id: str,
    generation: int,
    *,
    status: str,
    error: str | None = None,
    error_summary: str | None = None,
) -> None:
    """Publish a terminal state only when this execution still owns the job."""
    command = await conn.execute(
        """
        UPDATE knowledge.crawl_jobs
        SET status=$1,
            error=$2,
            error_summary=$3::jsonb,
            updated_at=extract(epoch FROM now())::bigint
        WHERE id=$4 AND execution_generation=$5 AND status='running'
        """,
        status,
        error,
        error_summary,
        job_id,
        generation,
    )
    if command == "UPDATE 0":
        raise CrawlExecutionSuperseded(job_id)


def _decode_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PostgresCrawlCheckpoint:
    """Store one crawl scope as small metadata plus per-URL frontier rows."""

    def __init__(
        self,
        conn: asyncpg.Connection,
        *,
        job_id: str,
        org_id: str,
        scope: str,
        generation: int,
    ) -> None:
        self.conn = conn
        self.job_id = job_id
        self.org_id = org_id
        self.scope = scope
        self.generation = generation

    async def _locked_job(self) -> Any:
        row = await self.conn.fetchrow(
            """
            SELECT execution_generation, status, runtime_checkpoint
            FROM knowledge.crawl_jobs
            WHERE id=$1
            FOR UPDATE
            """,
            self.job_id,
        )
        if row is None or int(row["execution_generation"]) != self.generation:
            raise CrawlExecutionSuperseded(self.job_id)
        if row["status"] != "running":
            raise CrawlExecutionSuperseded(self.job_id)
        return row

    async def ensure_active(self) -> None:
        await self._locked_job()

    async def load(self) -> dict[str, Any] | None:
        await self.ensure_active()
        parent = await self.conn.fetchrow(
            "SELECT runtime_checkpoint FROM knowledge.crawl_jobs WHERE id=$1",
            self.job_id,
        )
        runtime = _decode_json(parent["runtime_checkpoint"]) if parent else None
        metadata = (runtime or {}).get(self.scope)
        if not metadata:
            return None

        rows = await self.conn.fetch(
            """
            SELECT canonical_url, url, depth, discovered_from, source_kind,
                   priority, discovery_order, state, reason_code, result, outcome
            FROM knowledge.crawl_job_frontier
            WHERE job_id=$1 AND crawl_scope=$2
            ORDER BY discovery_order
            """,
            self.job_id,
            self.scope,
        )
        snapshot = dict(metadata)
        snapshot["ledger"] = [
            {
                "canonical_url": row["canonical_url"],
                "url": row["url"],
                "depth": row["depth"],
                "discovered_from": row["discovered_from"],
                "source_kind": row["source_kind"],
                "priority": row["priority"],
                "order": row["discovery_order"],
                "status": row["state"],
                "reason_code": row["reason_code"],
            }
            for row in rows
        ]
        snapshot["results"] = [
            _decode_json(row["result"]) for row in rows if row["result"] is not None
        ]
        snapshot["outcomes"] = [
            _decode_json(row["outcome"]) for row in rows if row["outcome"] is not None
        ]
        return snapshot

    async def save(self, snapshot: dict[str, Any]) -> None:
        from knowledge_ingest.crawl4ai_client import _canonicalise_url

        ledger = list(snapshot.get("ledger") or [])
        results_by_url = {
            _canonicalise_url(str(result.get("requested_url") or result["url"])): result
            for result in snapshot.get("results") or []
        }
        outcomes_by_url = {
            _canonicalise_url(str(outcome["url"])): outcome
            for outcome in snapshot.get("outcomes") or []
        }
        frontier_rows = [
            (
                self.job_id,
                self.org_id,
                self.scope,
                row["canonical_url"],
                row["url"],
                row["depth"],
                row.get("discovered_from"),
                row["source_kind"],
                row["priority"],
                row["order"],
                row["status"],
                row.get("reason_code"),
                json.dumps(results_by_url.get(row["canonical_url"]))
                if row["canonical_url"] in results_by_url
                else None,
                json.dumps(outcomes_by_url.get(row["canonical_url"]))
                if row["canonical_url"] in outcomes_by_url
                else None,
            )
            for row in ledger
        ]
        metadata = {
            key: value
            for key, value in snapshot.items()
            if key not in {"ledger", "results", "outcomes"}
        }

        async with self.conn.transaction():
            parent = await self._locked_job()
            if frontier_rows:
                await self.conn.executemany(
                    """
                    INSERT INTO knowledge.crawl_job_frontier (
                        job_id, org_id, crawl_scope, canonical_url, url, depth,
                        discovered_from, source_kind, priority, discovery_order,
                        state, reason_code, result, outcome
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13::jsonb, $14::jsonb
                    )
                    ON CONFLICT (job_id, crawl_scope, canonical_url) DO UPDATE SET
                        url=EXCLUDED.url,
                        depth=EXCLUDED.depth,
                        discovered_from=EXCLUDED.discovered_from,
                        source_kind=EXCLUDED.source_kind,
                        priority=EXCLUDED.priority,
                        discovery_order=EXCLUDED.discovery_order,
                        state=EXCLUDED.state,
                        reason_code=EXCLUDED.reason_code,
                        result=EXCLUDED.result,
                        outcome=EXCLUDED.outcome
                    """,
                    frontier_rows,
                )
            runtime = _decode_json(parent["runtime_checkpoint"]) or {}
            runtime[self.scope] = metadata
            command = await self.conn.execute(
                """
                UPDATE knowledge.crawl_jobs
                SET runtime_checkpoint=$1::jsonb,
                    checkpoint_sequence=checkpoint_sequence + 1,
                    checkpoint_updated_at=now(),
                    updated_at=extract(epoch FROM now())::bigint
                WHERE id=$2 AND execution_generation=$3 AND status='running'
                """,
                json.dumps(runtime),
                self.job_id,
                self.generation,
            )
            if command == "UPDATE 0":
                raise CrawlExecutionSuperseded(self.job_id)
