"""The per-connector crawl memory against real PostgreSQL.

Covers what the unit tests cannot: the upsert keeps an earlier try for an
address that was only seen and forgets what it is told to, the memory reads
nothing but its own table (a stored page never comes back), a remembered address is crawled although
tonight's sitemap no longer lists it, a resumed crawl keeps the rotation order
through the real checkpoint tables (which do not store visit times and must
accept the ``known`` source kind), and a connector purge or knowledge-base
delete removes the connector's rows.
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

from knowledge_ingest import crawl4ai_client, pg_store
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, CrawlMemoryState, CrawlResult
from knowledge_ingest.crawl_checkpoint import PostgresCrawlCheckpoint, claim_crawl_execution
from knowledge_ingest.crawl_memory import PostgresCrawlMemory

_DSN = os.environ.get("CRAWL_CHECKPOINT_TEST_DSN")
_ORG = "org-memory-test"
_JAN, _FEB, _MAR = (datetime(2026, month, 1, tzinfo=UTC) for month in (1, 2, 3))
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


async def _crawl(memory: PostgresCrawlMemory, **kwargs: Any) -> list[dict[str, Any]]:
    """One crawl, recorded the way the adapter records an accepted run."""
    state = CrawlMemoryState(remembered=await memory.load())
    _results, outcomes = await crawl4ai_client.crawl_site(start_url=_START, memory=state, **kwargs)
    async with _connection_factory() as conn:
        await memory.record(conn, state.rows, state.forget)
    return outcomes


async def _memory_keys(connector_id: str) -> set[str]:
    async with _connection_factory() as conn:
        rows = await conn.fetch(
            "SELECT canonical_url FROM knowledge.crawl_url_memory "
            "WHERE org_id=$1 AND connector_id=$2",
            _ORG,
            connector_id,
        )
    return {row["canonical_url"] for row in rows}


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
async def test_memory_keeps_visits_and_ignores_stored_pages() -> None:
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
        dropped = {"url": _address(2), "canonical_url": _address(2), "depth": 1}
        async with _connection_factory() as conn:
            await memory.record(conn, [{**row, "visited": True}, {**dropped, "visited": True}], [])
            await memory.record(conn, [{**row, "depth": 1, "visited": False}], [_address(2)])

        loaded = await memory.load()
        remembered = {r["canonical_url"]: r for r in loaded}

        assert set(remembered) == {_address(1)}
        assert remembered[_address(1)]["depth"] == 1
        assert remembered[_address(1)]["last_visited_at"] is not None
        # Both come from the first record; the second moves neither.
        assert (
            remembered[_address(1)]["first_seen_at"] == (remembered[_address(1)]["last_visited_at"])
        )
        # Only crawls fill the memory; a stored page never comes back from it.
        assert stored_url not in {r["url"] for r in loaded}
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
        first = await _crawl(_memory(connector_id), max_pages=6)
        _install_site(monkeypatch, [])
        second = await _crawl(_memory(connector_id), max_pages=6)

        def read(outcomes: list[dict[str, Any]]) -> set[str]:
            return {o["url"] for o in outcomes if o["reason_code"] == "success"} - {_START}

        assert len(read(first)) == 5
        assert read(second) == set(addresses) - read(first)
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
                    org_id, kb_slug, connector_id, canonical_url, url, depth,
                    first_seen_at, last_visited_at
                ) VALUES ($1, 'docs', $2, $3, $3, 1, $4, $5)
                """,
                [
                    (_ORG, connector_id, known_only, _JAN, _FEB),
                    *[(_ORG, connector_id, url, _JAN, _MAR) for url in sitemap[:3]],
                    *[(_ORG, connector_id, url, _JAN, None) for url in sitemap[3:5]],
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
                memory=CrawlMemoryState(remembered=await _memory(connector_id).load()),
            )

        with pytest.raises(asyncio.CancelledError):
            await _attempt()
        await _attempt()

        requested = [url for call in bulk_calls for url in call]
        # Never tried since January, tried in February, tried in March, then
        # the address the memory did not know before tonight.
        assert list(dict.fromkeys(requested)) == [
            *sitemap[3:5],
            known_only,
            *sitemap[:3],
            sitemap[5],
        ]
    finally:
        await _cleanup(connector_id, job_id)


@pytest.mark.asyncio
async def test_connector_purge_and_kb_delete_remove_the_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purged, deleted_kb = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        _install_site(monkeypatch, [_address(1)])
        await _crawl(_memory(purged), max_pages=5)
        await _crawl(_memory(deleted_kb), max_pages=5)
        assert await _memory_keys(purged)
        assert await _memory_keys(deleted_kb)

        async with _connection_factory() as conn:
            await pg_store.delete_connector_crawl_jobs(conn, _ORG, "docs", purged)
        assert await _memory_keys(purged) == set()
        assert await _memory_keys(deleted_kb)

        async with _connection_factory() as conn:
            await pg_store.delete_kb(conn, _ORG, "docs")
        assert await _memory_keys(deleted_kb) == set()
    finally:
        await _cleanup(purged)
        await _cleanup(deleted_kb)
