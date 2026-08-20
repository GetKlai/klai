"""Postgres-backed crawl checkpoints with execution-generation fencing."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import asyncpg

type ConnectionFactory = Callable[[], AbstractAsyncContextManager[asyncpg.Connection]]


class CrawlExecutionSuperseded(RuntimeError):
    """A newer worker attempt owns this crawl job."""


class CrawlExecutionCancelled(RuntimeError):
    """The operator cancelled this crawl job."""


class CrawlExecutionBusy(RuntimeError):
    """Another attempt is inside a fenced crawl side-effect block."""


async def claim_crawl_execution(conn: asyncpg.Connection, job_id: str) -> int:
    """Fence older attempts before this attempt performs network work."""
    acquired = await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 0))", job_id)
    if not acquired:
        raise CrawlExecutionBusy(f"crawl execution lock busy: {job_id}")
    try:
        row = await conn.fetchrow(
            """
            UPDATE knowledge.crawl_jobs
            SET execution_generation=execution_generation + 1,
                recovery_count=recovery_count + CASE WHEN status='running' THEN 1 ELSE 0 END,
                status='running',
                updated_at=extract(epoch FROM now())::bigint
            WHERE id=$1
              AND status IN ('pending', 'running')
              AND cancel_requested = false
            RETURNING execution_generation
            """,
            job_id,
        )
        if row is None:
            row = await conn.fetchrow(
                "SELECT status, cancel_requested FROM knowledge.crawl_jobs WHERE id=$1",
                job_id,
            )
            if row and row["cancel_requested"]:
                raise CrawlExecutionCancelled(job_id)
            raise CrawlExecutionSuperseded(job_id)
        return int(row["execution_generation"])
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
    acquired = await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 0))", job_id)
    if not acquired:
        raise CrawlExecutionBusy(f"crawl execution lock busy: {job_id}")
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
    """Publish terminal state and discard resume payload atomically."""
    async with conn.transaction():
        command = await conn.execute(
            """
            UPDATE knowledge.crawl_jobs
            SET status=$1,
                error=$2,
                error_summary=$3::jsonb,
                runtime_checkpoint='{}'::jsonb,
                checkpoint_updated_at=NULL,
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
        await conn.execute(
            "DELETE FROM knowledge.crawl_job_frontier WHERE job_id=$1",
            job_id,
        )


def _decode_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _encode_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class PostgresCrawlCheckpoint:
    """Store one crawl scope as small metadata plus per-URL frontier rows."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        job_id: str,
        org_id: str,
        scope: str,
        generation: int,
    ) -> None:
        self.connection_factory = connection_factory
        self.job_id = job_id
        self.org_id = org_id
        self.scope = scope
        self.generation = generation
        self._persisted_rows: dict[str, tuple[Any, ...]] = {}

    async def _locked_job(self, conn: asyncpg.Connection) -> Any:
        row = await conn.fetchrow(
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
        async with self.connection_factory() as conn:
            await ensure_crawl_execution_active(conn, self.job_id, self.generation)

    async def load(self) -> dict[str, Any] | None:
        async with self.connection_factory() as conn:
            async with conn.transaction():
                parent = await self._locked_job(conn)
                runtime = _decode_json(parent["runtime_checkpoint"]) or {}
                metadata = runtime.get(self.scope)
                if not metadata:
                    return None

                rows = await conn.fetch(
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

        self._persisted_rows = {
            row["canonical_url"]: (
                self.job_id,
                self.org_id,
                self.scope,
                row["canonical_url"],
                row["url"],
                row["depth"],
                row["discovered_from"],
                row["source_kind"],
                row["priority"],
                row["discovery_order"],
                row["state"],
                row["reason_code"],
                _encode_json(_decode_json(row["result"])),
                _encode_json(_decode_json(row["outcome"])),
            )
            for row in rows
        }
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
        from knowledge_ingest.crawl_url_policy import canonicalise_url

        ledger = list(snapshot.get("ledger") or [])
        results_by_url = {
            canonicalise_url(str(result.get("requested_url") or result["url"])): result
            for result in snapshot.get("results_delta") or []
        }
        outcomes_by_url = {
            canonicalise_url(str(outcome["url"])): outcome
            for outcome in snapshot.get("outcomes_delta") or []
        }
        frontier_rows_by_url: dict[str, tuple[Any, ...]] = {}
        for row in ledger:
            canonical_url = row["canonical_url"]
            persisted = self._persisted_rows.get(canonical_url)
            result_json = (
                _encode_json(results_by_url[canonical_url])
                if canonical_url in results_by_url
                else persisted[12]
                if persisted is not None
                else None
            )
            outcome_json = (
                _encode_json(outcomes_by_url[canonical_url])
                if canonical_url in outcomes_by_url
                else persisted[13]
                if persisted is not None
                else None
            )
            frontier_rows_by_url[canonical_url] = (
                self.job_id,
                self.org_id,
                self.scope,
                canonical_url,
                row["url"],
                row["depth"],
                row.get("discovered_from"),
                row["source_kind"],
                row["priority"],
                row["order"],
                row["status"],
                row.get("reason_code"),
                result_json,
                outcome_json,
            )
        changed_rows = [
            row
            for canonical_url, row in frontier_rows_by_url.items()
            if self._persisted_rows.get(canonical_url) != row
        ]
        metadata = {
            key: value
            for key, value in snapshot.items()
            if key not in {"ledger", "results_delta", "outcomes_delta"}
        }

        async with self.connection_factory() as conn:
            async with conn.transaction():
                parent = await self._locked_job(conn)
                if changed_rows:
                    await conn.executemany(
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
                        changed_rows,
                    )
                runtime = _decode_json(parent["runtime_checkpoint"]) or {}
                runtime[self.scope] = metadata
                command = await conn.execute(
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
        self._persisted_rows.update({row[3]: row for row in changed_rows})
