"""A crawl that cannot visit everything continues where the previous one stopped.

Three rules, each pinned below against a fake fetch layer and an in-memory
stand-in for the per-source visit memory:

1. ``max_pages`` counts addresses that answered with a page. An address that
   answered "does not exist" (404/410) is free against it, and a separate
   attempt bound ends a crawl whose address list is mostly dead.
2. The frontier visits what was visited longest ago first; an address that was
   dead at its last visit goes after all others.
3. Every finished connector crawl reports a ``coverage`` object.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.adapters.crawler import _build_crawl_outcome_warning, _crawl_coverage
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, CrawlResult
from knowledge_ingest.reason_codes import FetchReasonCode

_START = "https://example.com/"


def _address(index: int) -> str:
    return f"https://example.com/p/{index}"


def _page(url: str, status: int) -> dict[str, Any]:
    ok = status == 200
    return {
        "url": url,
        "success": ok,
        "status_code": status,
        "error_message": "" if ok else f"HTTP {status}",
        "html": f"<html><body>{url}</body></html>" if ok else "",
        "markdown": f"Page body for {url}" if ok else "",
        "links": {"internal": []},
        "media": {},
    }


class _Site:
    """A site whose sitemap lists ``live`` and ``dead`` addresses.

    ``rate_limit_after`` makes the site answer 429 once it has served that many
    pages in one night, the way a site that slows crawlers down behaves.
    """

    def __init__(
        self,
        *,
        live: list[str],
        dead: list[str] | None = None,
        sitemap: list[str] | None = None,
        rate_limit_after: int | None = None,
    ) -> None:
        self.live = set(live)
        self.dead = set(dead or [])
        self.sitemap = sitemap if sitemap is not None else [*(dead or []), *live]
        self.rate_limit_after = rate_limit_after
        self.served = 0
        self.bulk_calls: list[list[str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _sitemap(_base: str) -> list[str]:
            return list(self.sitemap)

        async def _seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
            self.served += 1
            return CrawlResult(
                url=start_url,
                fit_markdown="Start page",
                raw_markdown="Start page",
                html="<html><body>Start page</body></html>",
                word_count=2,
                success=True,
                status_code=200,
                links={"internal": []},
            )

        async def _bulk(*, urls: list[str], **_kwargs: Any) -> ChunkedFetchResult:
            self.bulk_calls.append(list(urls))
            raw: list[dict[str, Any]] = []
            for index, url in enumerate(urls):
                if self.rate_limit_after is not None and self.served >= self.rate_limit_after:
                    return ChunkedFetchResult(
                        raw_results=[*raw, _page(url, 429)],
                        not_attempted=urls[index + 1 :],
                        stopped_early=True,
                        stop_trigger_reason_code=FetchReasonCode.RATE_LIMITED.value,
                    )
                if url in self.dead:
                    raw.append(_page(url, 404))
                else:
                    self.served += 1
                    raw.append(_page(url, 200))
            return ChunkedFetchResult(raw_results=raw)

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _sitemap)
        monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _seed)
        monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _bulk)
        monkeypatch.setattr(crawl4ai_client, "_slowdown_sleep", _no_sleep)

    def new_night(self) -> None:
        self.served = 0
        self.bulk_calls = []


class _FakeMemory:
    """Same semantics as ``PostgresCrawlMemory``; one save is one point in time."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.clock = 0.0
        self.loads = 0
        self.saves = 0

    async def load(self) -> list[dict[str, Any]]:
        self.loads += 1
        return [dict(row) for row in self.rows.values()]

    async def save(self, rows: list[dict[str, Any]]) -> None:
        self.saves += 1
        self.clock += 1
        for row in rows:
            old = self.rows.get(row["canonical_url"])
            self.rows[row["canonical_url"]] = {
                "canonical_url": row["canonical_url"],
                "url": row["url"],
                "depth": min(row["depth"], old["depth"]) if old else row["depth"],
                "last_visited_at": self.clock
                if row["visited"]
                else (old["last_visited_at"] if old else None),
                "last_reason_code": row["reason_code"]
                if row["visited"]
                else (old["last_reason_code"] if old else None),
            }


class _FrontierTableCheckpoint:
    """Keeps only the ledger columns ``crawl_job_frontier`` stores.

    ``PostgresCrawlCheckpoint`` persists a fixed set of frontier columns, so a
    resumed crawl must not rely on anything else surviving in the ledger rows.
    """

    _COLUMNS = frozenset(
        {
            "canonical_url",
            "url",
            "depth",
            "discovered_from",
            "source_kind",
            "priority",
            "order",
            "status",
            "reason_code",
        }
    )

    def __init__(self) -> None:
        self.snapshot: dict[str, Any] | None = None

    async def load(self) -> dict[str, Any] | None:
        return copy.deepcopy(self.snapshot)

    async def save(self, snapshot: dict[str, Any]) -> None:
        previous = self.snapshot or {}
        self.snapshot = {
            **{k: v for k, v in snapshot.items() if k not in {"results_delta", "outcomes_delta"}},
            "ledger": [
                {k: v for k, v in row.items() if k in self._COLUMNS} for row in snapshot["ledger"]
            ],
            "results": list(previous.get("results") or [])
            + copy.deepcopy(snapshot.get("results_delta") or []),
            "outcomes": list(previous.get("outcomes") or [])
            + copy.deepcopy(snapshot.get("outcomes_delta") or []),
        }

    async def ensure_active(self) -> None:
        return None


def _read(outcomes: list[dict[str, Any]]) -> set[str]:
    return {o["url"] for o in outcomes if o["reason_code"] == FetchReasonCode.SUCCESS.value}


async def _night(site: _Site, memory: _FakeMemory, *, max_pages: int = 500) -> Any:
    site.new_night()
    return await crawl4ai_client.crawl_site(start_url=_START, max_pages=max_pages, memory=memory)


@pytest.mark.asyncio
async def test_dead_addresses_do_not_use_up_the_page_limit(monkeypatch) -> None:
    """(a) 620 dead and 117 live addresses with a limit of 500: one crawl gets every page."""
    live = [_address(i) for i in range(116)]
    dead = [_address(1000 + i) for i in range(620)]
    site = _Site(live=live, dead=dead)
    site.install(monkeypatch)

    results, outcomes = await _night(site, _FakeMemory())

    assert {r.url for r in results} == {_START, *live}
    coverage = _crawl_coverage(outcomes, max_pages=500)
    assert coverage == {
        "pages_read": 117,
        "addresses_gone": 620,
        "addresses_waiting": 0,
        "addresses_known": 737,
        "max_pages": 500,
        "stopped_by": None,
    }


@pytest.mark.asyncio
async def test_consecutive_crawls_rotate_through_a_site_larger_than_the_limit(
    monkeypatch,
) -> None:
    """(b) Four crawls of 500 together visit every address once; the fifth starts over.

    The start address is read every night, so the rotating part of each crawl
    is 499 addresses: 1,996 addresses take exactly four nights.
    """
    live = [_address(i) for i in range(1996)]
    site = _Site(live=live)
    site.install(monkeypatch)
    memory = _FakeMemory()

    nights = []
    for _ in range(5):
        _results, outcomes = await _night(site, memory)
        nights.append(outcomes)

    reads = [_read(outcomes) - {_START} for outcomes in nights]
    assert [len(r) for r in reads[:4]] == [499, 499, 499, 499]
    assert set().union(*reads[:4]) == set(live)
    assert sum(len(r) for r in reads[:4]) == len(live)
    assert reads[4] == reads[0]
    assert _crawl_coverage(nights[0], max_pages=500) == {
        "pages_read": 500,
        "addresses_gone": 0,
        "addresses_waiting": 1497,
        "addresses_known": 1997,
        "max_pages": 500,
        "stopped_by": "page_limit",
    }


@pytest.mark.asyncio
async def test_crawl_after_a_rate_limited_crawl_starts_with_what_it_did_not_reach(
    monkeypatch,
) -> None:
    """(c) A site that answers 429 after 30 pages every night still gets fully read."""
    live = [_address(i) for i in range(99)]
    site = _Site(live=live, rate_limit_after=30)
    site.install(monkeypatch)
    memory = _FakeMemory()

    _results, first = await _night(site, memory)
    first_read = _read(first)
    _results, second = await _night(site, memory)

    unreached = set(live) - first_read
    assert len(first_read) == 30
    assert set(site.bulk_calls[0][: len(unreached)]) == unreached
    assert (_read(second) - {_START}).isdisjoint(first_read)
    assert _crawl_coverage(first, max_pages=500) == {
        "pages_read": 30,
        "addresses_gone": 0,
        "addresses_waiting": 70,
        "addresses_known": 100,
        "max_pages": 500,
        "stopped_by": "site_rate_limit",
    }


@pytest.mark.asyncio
async def test_an_address_dead_at_its_last_visit_goes_last_and_returns_when_it_answers(
    monkeypatch,
) -> None:
    """(d) A 404 is retried after the others, and stored again once it is back."""
    returning = _address(0)
    others = [_address(i) for i in range(1, 4)]
    site = _Site(live=others, dead=[returning], sitemap=[returning, *others])
    site.install(monkeypatch)
    memory = _FakeMemory()

    await _night(site, memory)
    site.dead.clear()
    site.live.add(returning)
    results, _outcomes = await _night(site, memory)

    assert site.bulk_calls[0] == [*others, returning]
    assert returning in {r.url for r in results}


@pytest.mark.asyncio
async def test_a_list_of_only_dead_addresses_ends_at_the_attempt_bound(monkeypatch) -> None:
    """(e) Dead addresses are free against the page limit, but not endless."""
    dead = [_address(i) for i in range(1500)]
    site = _Site(live=[], dead=dead)
    site.install(monkeypatch)

    _results, outcomes = await _night(site, _FakeMemory())

    attempted = [url for call in site.bulk_calls for url in call]
    assert len(attempted) == 999
    waiting = [o for o in outcomes if o["reason_code"].startswith("not_fetched_")]
    assert len(waiting) == 1500 - 999
    assert {(o["reason_code"], o["budget"]) for o in waiting} == {
        (FetchReasonCode.NOT_FETCHED_BUDGET_EXHAUSTED.value, "attempts")
    }
    warning = _build_crawl_outcome_warning(outcomes, max_pages=500)
    assert warning is not None
    assert warning["reason"] == "crawl_addresses_mostly_gone"
    assert _crawl_coverage(outcomes, max_pages=500)["stopped_by"] == "attempt_limit"


@pytest.mark.asyncio
async def test_a_resumed_crawl_keeps_the_rotation_order(monkeypatch) -> None:
    """(f) A deploy halfway through a crawl does not reset the order to discovery order."""
    addresses = [_address(i) for i in range(6)]
    site = _Site(live=addresses)
    site.install(monkeypatch)
    monkeypatch.setattr(crawl4ai_client, "_CHECKPOINT_BATCH_SIZE", 2)
    memory = _FakeMemory()
    for url in addresses[:3]:
        memory.rows[url] = {
            "canonical_url": url,
            "url": url,
            "depth": 1,
            "last_visited_at": 0.0,
            "last_reason_code": FetchReasonCode.SUCCESS.value,
        }
    expected = [*addresses[3:], *addresses[:3]]

    real_bulk = crawl4ai_client._chunked_bulk_fetch
    calls = 0

    async def _interrupt_second_batch(**kwargs: Any) -> ChunkedFetchResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            site.bulk_calls.append(list(kwargs["urls"]))
            raise asyncio.CancelledError
        return await real_bulk(**kwargs)

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _interrupt_second_batch)
    checkpoint = _FrontierTableCheckpoint()
    with pytest.raises(asyncio.CancelledError):
        await crawl4ai_client.crawl_site(
            start_url=_START, max_pages=500, checkpoint=checkpoint, memory=memory
        )
    assert memory.saves == 0
    await crawl4ai_client.crawl_site(
        start_url=_START, max_pages=500, checkpoint=checkpoint, memory=memory
    )

    requested = [url for call in site.bulk_calls for url in call]
    assert list(dict.fromkeys(requested)) == expected


@pytest.mark.asyncio
async def test_the_crawl_job_summary_carries_coverage_for_a_clean_crawl() -> None:
    """(g) A crawl with nothing to warn about still reports how far it got."""
    from tests.conftest import connection_factory_for

    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    outcomes = [
        {"url": _START, "reason_code": "success", "status_code": 200},
        {"url": _address(1), "reason_code": "gone", "status_code": 404},
    ]
    result = CrawlResult(
        url=_START,
        fit_markdown="Start page",
        raw_markdown="Start page",
        html="<html></html>",
        word_count=2,
        success=True,
    )
    finished = AsyncMock()
    pg = MagicMock()
    pg.get_crawled_page_hashes = AsyncMock(return_value={})
    pg.get_crawled_pages_markdown = AsyncMock(return_value=[])
    pg.get_active_document_texts = AsyncMock(return_value={})
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", pg),
        patch("knowledge_ingest.adapters.crawler.finish_crawl_execution", finished),
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new_callable=AsyncMock,
            return_value=([result], outcomes),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "knowledge_ingest.adapters.crawler._enqueue_taxonomy_backfill_after_crawl",
            new_callable=AsyncMock,
        ),
        patch("knowledge_ingest.adapters.crawler._build_link_graph", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.adapters.crawler._crawl_fully_fetched",
            return_value=False,
        ),
    ):
        from knowledge_ingest.adapters.crawler import run_crawl_job

        await run_crawl_job(
            connection_factory=connection_factory_for(conn),
            job_id="job-1",
            org_id="org-1",
            kb_slug="docs",
            start_url=_START,
            max_pages=500,
            rate_limit=100.0,
            connector_id="conn-1",
        )

    import json

    kwargs = finished.await_args.kwargs
    assert kwargs["status"] == "completed"
    assert json.loads(kwargs["error_summary"])["coverage"] == {
        "pages_read": 1,
        "addresses_gone": 1,
        "addresses_waiting": 0,
        "addresses_known": 2,
        "max_pages": 500,
        "stopped_by": None,
    }


def test_the_status_payload_exposes_coverage() -> None:
    """(g) The connector service reads coverage from the status route."""
    from tests.test_crawl_sync_endpoint import _client_with_patches, _make_pool

    coverage = {
        "pages_read": 30,
        "addresses_gone": 0,
        "addresses_waiting": 70,
        "addresses_known": 100,
        "max_pages": 500,
        "stopped_by": "site_rate_limit",
    }
    pool = _make_pool(
        job_row={
            "status": "failed_partial",
            "pages_total": 30,
            "pages_done": 30,
            "error": None,
            "error_summary": {"reason": "crawl_budget_exhausted", "coverage": coverage},
        },
    )
    with _client_with_patches(pool) as (client, _defer):
        body = client.get("/ingest/v1/crawl/sync/job-1/status").json()
    assert body["coverage"] == coverage


def test_a_preview_crawl_neither_reads_nor_writes_the_memory(client: TestClient) -> None:
    """(h) Previewing a source must not move its rotation."""
    page = CrawlResult(
        url="https://example.com/docs",
        fit_markdown="Real article prose about the product. " * 40,
        raw_markdown="Real article prose about the product. " * 40,
        html="<html>" + "<p>text</p>" * 200 + "</html>",
        word_count=240,
        success=True,
        metadata={"status_code": 200},
        response_headers={},
    )
    with (
        patch("knowledge_ingest.routes.crawl.crawl_page", new=AsyncMock(return_value=page)),
        patch(
            "knowledge_ingest.crawl_memory.PostgresCrawlMemory.load", new_callable=AsyncMock
        ) as load,
        patch(
            "knowledge_ingest.crawl_memory.PostgresCrawlMemory.save", new_callable=AsyncMock
        ) as save,
    ):
        resp = client.post("/ingest/v1/crawl/preview", json={"url": "https://example.com/docs"})

    assert resp.status_code == 200, resp.text
    load.assert_not_awaited()
    save.assert_not_awaited()
