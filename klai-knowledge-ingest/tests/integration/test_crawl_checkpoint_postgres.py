"""Real-Postgres fault injection for durable crawl checkpoints."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.crawl_checkpoint import (
    CrawlExecutionBusy,
    PostgresCrawlCheckpoint,
    claim_crawl_execution,
)

_DSN = os.environ.get("CRAWL_CHECKPOINT_TEST_DSN")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.no_mock_db_helpers,
    pytest.mark.skipif(not _DSN, reason="CRAWL_CHECKPOINT_TEST_DSN is not configured"),
]


@asynccontextmanager
async def _connection_factory():
    assert _DSN is not None
    conn = await asyncpg.connect(_DSN)
    try:
        await conn.execute("SELECT set_config('app.current_org_id', 'org-test', false)")
        await conn.execute("SELECT set_config('app.cross_org_admin', 'false', false)")
        yield conn
    finally:
        await conn.close()


async def _insert_job(job_id: str, *, generation: int = 0) -> None:
    async with _connection_factory() as conn:
        await conn.execute(
            """
            INSERT INTO knowledge.crawl_jobs (
                id, org_id, kb_slug, config, status, created_at, updated_at,
                execution_generation
            ) VALUES ($1, 'org-test', 'kb-test', '{}'::jsonb, 'pending', $2, $2, $3)
            """,
            job_id,
            int(time.time()),
            generation,
        )


@pytest.mark.asyncio
async def test_generation_is_database_monotonic_and_busy_claim_does_not_wait() -> None:
    job_id = "checkpoint-generation"
    await _insert_job(job_id, generation=9_000_000_000_000_000_000)
    try:
        async with _connection_factory() as conn:
            generation = await claim_crawl_execution(conn, job_id)
        assert generation == 9_000_000_000_000_000_001

        async with _connection_factory() as lock_conn:
            await lock_conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", job_id)
            async with _connection_factory() as contender:
                with pytest.raises(CrawlExecutionBusy):
                    async with asyncio.timeout(0.5):
                        await claim_crawl_execution(contender, job_id)
    finally:
        async with _connection_factory() as conn:
            await conn.execute("DELETE FROM knowledge.crawl_jobs WHERE id=$1", job_id)


@pytest.mark.asyncio
async def test_interrupted_crawl_resumes_from_real_postgres_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_id = "checkpoint-resume"
    start_url = "https://example.com"
    urls = [f"https://example.com/page-{index}" for index in range(1, 4)]
    await _insert_job(job_id)

    async def _sitemap(_base: str) -> list[str]:
        return urls

    async def _seed(**_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=start_url,
            fit_markdown="seed",
            raw_markdown="seed",
            html="<p>seed</p>",
            word_count=1,
            success=True,
            links={"internal": []},
        )

    requested: list[str] = []
    calls = 0

    async def _bulk(*, urls: list[str], **_kwargs: Any):
        nonlocal calls
        calls += 1
        requested.extend(urls)
        if calls == 2:
            raise asyncio.CancelledError
        return crawl4ai_client.ChunkedFetchResult(
            raw_results=[
                {
                    "url": url,
                    "success": True,
                    "status_code": 200,
                    "html": f"<p>{url}</p>",
                    "markdown": url,
                    "links": {"internal": []},
                    "media": {},
                }
                for url in urls
            ]
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _sitemap)
    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _seed)
    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _bulk)
    monkeypatch.setattr(crawl4ai_client, "_CHECKPOINT_BATCH_SIZE", 2)

    try:
        async with _connection_factory() as conn:
            first_generation = await claim_crawl_execution(conn, job_id)
        first_checkpoint = PostgresCrawlCheckpoint(
            _connection_factory,
            job_id=job_id,
            org_id="org-test",
            scope="primary",
            generation=first_generation,
        )
        with pytest.raises(asyncio.CancelledError):
            await crawl4ai_client.crawl_site(
                start_url=start_url,
                max_pages=4,
                checkpoint=first_checkpoint,
            )

        async with _connection_factory() as conn:
            second_generation = await claim_crawl_execution(conn, job_id)
        resumed_checkpoint = PostgresCrawlCheckpoint(
            _connection_factory,
            job_id=job_id,
            org_id="org-test",
            scope="primary",
            generation=second_generation,
        )
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url=start_url,
            max_pages=4,
            checkpoint=resumed_checkpoint,
        )

        assert requested.count(urls[0]) == 1
        assert requested.count(urls[1]) == 1
        assert requested.count(urls[2]) == 2
        assert {result.url for result in results} == {start_url, *urls}
        assert {outcome["url"] for outcome in outcomes} == {start_url, *urls}
        async with _connection_factory() as conn:
            row = await conn.fetchrow(
                """
                SELECT checkpoint_sequence, recovery_count,
                       (SELECT count(*) FROM knowledge.crawl_job_frontier
                        WHERE job_id=$1) AS frontier_count
                FROM knowledge.crawl_jobs
                WHERE id=$1
                """,
                job_id,
            )
        assert row["checkpoint_sequence"] >= 4
        assert row["recovery_count"] == 1
        assert row["frontier_count"] == 4
    finally:
        async with _connection_factory() as conn:
            await conn.execute("DELETE FROM knowledge.crawl_jobs WHERE id=$1", job_id)
