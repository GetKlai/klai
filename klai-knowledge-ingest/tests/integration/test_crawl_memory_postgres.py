"""The per-connector crawl memory against real PostgreSQL.

Covers what the unit tests cannot: the upsert keeps an earlier visit for an
address that was only seen, stored pages join the frontier, a remembered
address is crawled although tonight's sitemap no longer lists it, and a
resumed crawl keeps the rotation order through the real checkpoint tables
(which do not store visit times and must accept the ``known`` source kind).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, CrawlResult
from knowledge_ingest.crawl_checkpoint import PostgresCrawlCheckpoint, claim_crawl_execution
from knowledge_ingest.crawl_memory import PostgresCrawlMemory

_DSN = os.environ.get("CRAWL_CHECKPOINT_TEST_DSN")
_ORG = "org-memory-test"
_START = "https://example.com/"

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
        await conn.execute("SELECT set_config('app.current_org_id', $1, false)", _ORG)
        yield conn
    finally:
        await conn.close()


def _address(index: int) -> str:
    return f"https://example.com/p/{index}"


def _install_site(monkeypatch: pytest.MonkeyPatch, sitemap: list[str]) -> list[list[str]]:
    bulk_calls: list[list[str]] = []

    async def _sitemap(_base: str) -> list[str]:
        return list(sitemap)

    async def _seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=start_url,
            fit_markdown="Start page",
            raw_markdown="Start page",
            html="<p>Start page</p>",
            word_count=2,
            success=True,
            status_code=200,
            links={"internal": []},
        )

    async def _bulk(*, urls: list[str], **_kwargs: Any) -> ChunkedFetchResult:
        bulk_calls.append(list(urls))
        return ChunkedFetchResult(
            raw_results=[
                {
                    "url": url,
                    "success": True,
                    "status_code": 200,
                    "html": f"<p>{url}</p>",
                    "markdown": f"Page body for {url}",
                    "links": {"internal": []},
                    "media": {},
                }
                for url in urls
            ]
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _sitemap)
    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _seed)
    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _bulk)
    return bulk_calls


def _memory(connector_id: str) -> PostgresCrawlMemory:
    return PostgresCrawlMemory(
        _connection_factory, org_id=_ORG, kb_slug="docs", connector_id=connector_id
    )


async def _cleanup(connector_id: str, job_id: str | None = None) -> None:
    async with _connection_factory() as conn:
        await conn.execute(
            "DELETE FROM knowledge.crawl_url_memory WHERE org_id=$1 AND connector_id=$2",
            _ORG,
            connector_id,
        )
        await conn.execute(
            "DELETE FROM knowledge.artifacts WHERE org_id=$1 AND extra::jsonb->>"
            "'source_connector_id' = $2",
            _ORG,
            connector_id,
        )
        if job_id:
            await conn.execute("DELETE FROM knowledge.crawl_jobs WHERE id=$1", job_id)


@pytest.mark.asyncio
async def test_memory_keeps_visits_and_knows_stored_pages() -> None:
    connector_id = str(uuid.uuid4())
    memory = _memory(connector_id)
    stored_url = _address(99)
    try:
        async with _connection_factory() as conn:
            await conn.execute(
                """
                INSERT INTO knowledge.artifacts (
                    id, org_id, provenance_type, assertion_mode, belief_time_start,
                    belief_time_end, created_at, kb_slug, path, extra
                ) VALUES ($1, $2, 'observed', 'factual', 1, 253402300800, 1, 'docs', $3,
                          $4::jsonb)
                """,
                str(uuid.uuid4()),
                _ORG,
                stored_url,
                json.dumps({"source_connector_id": connector_id, "source_url": stored_url}),
            )
        row = {"url": _address(1), "canonical_url": _address(1), "depth": 2}
        await memory.save([{**row, "visited": True, "reason_code": "gone"}])
        await memory.save([{**row, "depth": 1, "visited": False, "reason_code": None}])

        rows = {r["canonical_url"]: r for r in await memory.load()}

        assert rows[_address(1)]["depth"] == 1
        assert rows[_address(1)]["last_reason_code"] == "gone"
        assert rows[_address(1)]["last_visited_at"] is not None
        assert rows[stored_url]["last_visited_at"] is None
        assert rows[stored_url]["depth"] == 1
    finally:
        await _cleanup(connector_id)


@pytest.mark.asyncio
async def test_second_crawl_reaches_remembered_addresses_the_sitemap_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector_id = str(uuid.uuid4())
    addresses = [_address(i) for i in range(10)]
    try:
        _install_site(monkeypatch, addresses)
        _results, first = await crawl4ai_client.crawl_site(
            start_url=_START, max_pages=6, memory=_memory(connector_id)
        )
        _install_site(monkeypatch, [])
        _results, second = await crawl4ai_client.crawl_site(
            start_url=_START, max_pages=6, memory=_memory(connector_id)
        )

        def read(outcomes: list[dict[str, Any]]) -> set[str]:
            return {o["url"] for o in outcomes if o["reason_code"] == "success"} - {_START}

        assert len(read(first)) == 5
        assert read(first) | read(second) == set(addresses)
    finally:
        await _cleanup(connector_id)


@pytest.mark.asyncio
async def test_resumed_crawl_keeps_rotation_order_through_real_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connector_id = str(uuid.uuid4())
    job_id = f"memory-resume-{uuid.uuid4()}"
    sitemap = [_address(i) for i in range(6)]
    known_only = _address(6)
    try:
        async with _connection_factory() as conn:
            await conn.execute(
                """
                INSERT INTO knowledge.crawl_jobs (
                    id, org_id, kb_slug, config, status, created_at, updated_at
                ) VALUES ($1, $2, 'docs', '{}'::jsonb, 'pending', $3, $3)
                """,
                job_id,
                _ORG,
                int(time.time()),
            )
            await conn.executemany(
                """
                INSERT INTO knowledge.crawl_url_memory (
                    org_id, connector_id, canonical_url, url, depth,
                    last_visited_at, last_reason_code
                ) VALUES ($1, $2, $3, $3, 1, $4, 'success')
                """,
                [
                    (_ORG, connector_id, known_only, datetime(2026, 1, 1, tzinfo=UTC)),
                    *[
                        (_ORG, connector_id, url, datetime(2026, 2, 1, tzinfo=UTC))
                        for url in sitemap[:3]
                    ],
                ],
            )
        bulk_calls = _install_site(monkeypatch, sitemap)
        monkeypatch.setattr(crawl4ai_client, "_CHECKPOINT_BATCH_SIZE", 2)
        real_bulk = crawl4ai_client._chunked_bulk_fetch
        calls = 0

        async def _interrupt_second_batch(**kwargs: Any) -> ChunkedFetchResult:
            nonlocal calls
            calls += 1
            if calls == 2:
                bulk_calls.append(list(kwargs["urls"]))
                raise asyncio.CancelledError
            return await real_bulk(**kwargs)

        monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _interrupt_second_batch)

        async def _attempt() -> Any:
            async with _connection_factory() as conn:
                generation = await claim_crawl_execution(conn, job_id)
            return await crawl4ai_client.crawl_site(
                start_url=_START,
                max_pages=500,
                checkpoint=PostgresCrawlCheckpoint(
                    _connection_factory,
                    job_id=job_id,
                    org_id=_ORG,
                    scope="primary",
                    generation=generation,
                ),
                memory=_memory(connector_id),
            )

        with pytest.raises(asyncio.CancelledError):
            await _attempt()
        await _attempt()

        requested = [url for call in bulk_calls for url in call]
        assert list(dict.fromkeys(requested)) == [*sitemap[3:], known_only, *sitemap[:3]]
    finally:
        await _cleanup(connector_id, job_id)
