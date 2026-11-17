"""A crawl that cannot visit everything continues where the previous one stopped.

Pinned here against a fake fetch layer and an in-memory stand-in for the
per-connector crawl memory (``crawl_site`` reads the remembered addresses and
hands back what it tried; the adapter records it, see
``test_crawl_memory_adapter.py``):

1. ``max_pages`` counts addresses that answered with a page. An address that
   answered 404/410 is free against it, and a separate attempt bound ends a
   crawl whose address list is mostly dead.
2. Every address that was sent counts as tried, whatever it answered. Whoever
   has waited longest goes first: since its last try, or since the memory
   first saw it.
3. A remembered address that no sitemap entry or page links to tonight is
   forgotten when it answers 404/410.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.adapters.crawler import _build_crawl_outcome_warning, _crawl_coverage
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, CrawlMemoryState, CrawlResult
from knowledge_ingest.reason_codes import FetchReasonCode

_START = "https://example.com/"


def _address(index: int | str) -> str:
    return f"https://example.com/p/{index}"


def _page(url: str, status: int, links: list[str] | None = None) -> dict[str, Any]:
    ok = status == 200
    page = {
        "url": url,
        "success": ok,
        "status_code": status,
        "error_message": "" if ok else f"HTTP {status}",
        "html": f"<html><body>{url}</body></html>" if ok else "",
        "markdown": f"Page body for {url}" if ok else "",
        "links": {"internal": [{"href": h, "text": ""} for h in links or []]},
        "media": {},
    }
    if status == 0:
        page.update(status_code=None, error_message="Blocked by anti-bot protection: JS challenge")
    return page


class _Site:
    """A site whose sitemap lists ``live`` and ``dead`` addresses.

    ``blocked`` addresses always answer with an anti-bot block.
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
        blocked: list[str] | None = None,
        links: dict[str, list[str]] | None = None,
    ) -> None:
        self.live = set(live)
        self.dead = set(dead or [])
        self.blocked = set(blocked or [])
        self.sitemap = sitemap if sitemap is not None else [*(dead or []), *(blocked or []), *live]
        self.rate_limit_after = rate_limit_after
        self.links = links or {}
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
                links={
                    "internal": [{"href": h, "text": ""} for h in self.links.get(start_url, [])]
                },
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
                elif url in self.blocked:
                    raw.append(_page(url, 0))
                else:
                    self.served += 1
                    raw.append(_page(url, 200, self.links.get(url)))
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
    """Same semantics as ``PostgresCrawlMemory``; one record is one point in time."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.clock = 0.0

    async def load(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows.values()]

    async def record(self, _conn: Any, rows: list[dict[str, Any]], forget: list[str]) -> None:
        self.clock += 1
        for key in forget:
            self.rows.pop(key, None)
        for row in rows:
            old = self.rows.get(row["canonical_url"])
            self.rows[row["canonical_url"]] = {
                "canonical_url": row["canonical_url"],
                "url": row["url"],
                "depth": min(row["depth"], old["depth"]) if old else row["depth"],
                "first_seen_at": old["first_seen_at"] if old else self.clock,
                "last_visited_at": self.clock
                if row["visited"]
                else (old["last_visited_at"] if old else None),
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


async def _night(
    site: _Site, memory: _FakeMemory, *, max_pages: int = 500, max_depth: int = 2
) -> tuple[list[CrawlResult], list[dict[str, Any]], CrawlMemoryState]:
    """One crawl, recorded the way the adapter records an accepted run."""
    site.new_night()
    state = CrawlMemoryState(remembered=await memory.load())
    results, outcomes = await crawl4ai_client.crawl_site(
        start_url=_START, max_pages=max_pages, max_depth=max_depth, memory=state
    )
    await memory.record(None, state.rows, state.forget)
    return results, outcomes, state


@pytest.mark.asyncio
async def test_dead_addresses_do_not_use_up_the_page_limit(monkeypatch) -> None:
    """(a) 620 dead and 117 live addresses with a limit of 500: one crawl gets every page."""
    live = [_address(i) for i in range(116)]
    dead = [_address(1000 + i) for i in range(620)]
    site = _Site(live=live, dead=dead)
    site.install(monkeypatch)

    results, outcomes, _state = await _night(site, _FakeMemory())

    assert {r.url for r in results} == {_START, *live}
    assert _crawl_coverage(outcomes, max_pages=500) == {
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
    """(b) Four crawls of 500 together read every address once; the fifth starts over.

    The start address is read every night, so the rotating part of each crawl
    is 499 addresses: 1,996 addresses take exactly four nights.
    """
    live = [_address(i) for i in range(1996)]
    site = _Site(live=live)
    site.install(monkeypatch)
    memory = _FakeMemory()

    nights = []
    for _ in range(5):
        _results, outcomes, _state = await _night(site, memory)
        nights.append(outcomes)

    reads = [_read(outcomes) - {_START} for outcomes in nights]
    assert [len(r) for r in reads[:4]] == [499, 499, 499, 499]
    assert set().union(*reads[:4]) == set(live)
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
async def test_a_known_page_never_read_is_read_before_any_page_is_re_read(
    monkeypatch,
) -> None:
    """100 known addresses at 30 a night: nothing is read twice before all are read."""
    live = [_address(i) for i in range(100)]
    site = _Site(live=live)
    site.install(monkeypatch)
    memory = _FakeMemory()

    reads = []
    for _ in range(4):
        _results, outcomes, _state = await _night(site, memory, max_pages=31)
        reads.append(_read(outcomes) - {_START})

    assert sum(len(r) for r in reads[:3]) == len(set().union(*reads[:3])) == 90
    assert set(live) - set().union(*reads[:3]) <= reads[3]


@pytest.mark.asyncio
async def test_crawl_after_a_rate_limited_crawl_reads_only_what_it_did_not(
    monkeypatch,
) -> None:
    """(c) A site that answers 429 after 30 pages every night is still read further."""
    live = [_address(i) for i in range(99)]
    site = _Site(live=live, rate_limit_after=30)
    site.install(monkeypatch)
    memory = _FakeMemory()

    _results, first, _state = await _night(site, memory)
    first_read = _read(first)
    _results, second, _state = await _night(site, memory)

    assert set(memory.rows) == {_START, *live}
    assert len(first_read) == 30
    assert len(_read(second) - {_START}) == 29
    assert (_read(second) - {_START}).isdisjoint(first_read)
    _results, third, _state = await _night(site, memory)
    assert (_read(third) - {_START}).isdisjoint(first_read | _read(second))
    assert _crawl_coverage(first, max_pages=500) == {
        "pages_read": 30,
        "addresses_gone": 0,
        "addresses_waiting": 66,
        "addresses_known": 100,
        "max_pages": 500,
        "stopped_by": "site_rate_limit",
    }


@pytest.mark.asyncio
async def test_an_always_blocked_address_is_not_first_the_next_night(monkeypatch) -> None:
    """A block is an answer: the address was tried and goes round like the others."""
    blocked = _address("blocked")
    others = [_address(i) for i in range(9)]
    site = _Site(live=others, blocked=[blocked], sitemap=[blocked, *others])
    site.install(monkeypatch)
    memory = _FakeMemory()

    await _night(site, memory, max_pages=4)
    await _night(site, memory, max_pages=4)

    assert site.bulk_calls[0][0] != blocked


@pytest.mark.asyncio
async def test_a_dead_address_on_a_limit_filled_site_is_retried_within_a_round(
    monkeypatch,
) -> None:
    """A 404 rotates like any address and is stored again once it answers a page."""
    returning = _address("returning")
    others = [_address(i) for i in range(8)]
    site = _Site(live=others, dead=[returning], sitemap=[returning, *others])
    site.install(monkeypatch)
    memory = _FakeMemory()

    await _night(site, memory, max_pages=5)
    site.dead.clear()
    site.live.add(returning)
    # Nine known addresses at four a night: one round is three nights.
    read: set[str] = set()
    for _ in range(2):
        results, _outcomes, _state = await _night(site, memory, max_pages=5)
        read |= {r.url for r in results}

    assert returning in read


@pytest.mark.asyncio
async def test_endless_new_links_do_not_stop_the_refresh(monkeypatch) -> None:
    """A site minting 500 new addresses a night: what is known today is tried within a round.

    New addresses queue behind everything that waited longer. With 100 pages
    a night, the 100 stored pages known after the first night are all tried
    the next night, and the 600 known after that within the six nights after.
    """
    stored = [_address(f"s{i}") for i in range(100)]
    site = _Site(live=stored)
    site.install(monkeypatch)
    memory = _FakeMemory()
    await _night(site, memory, max_pages=101)

    def mint(night: int) -> None:
        minted = [_address(f"n{night}-{i}") for i in range(500)]
        site.live |= set(minted)
        site.sitemap = [*minted, *site.sitemap]

    mint(2)
    _results, outcomes, _state = await _night(site, memory, max_pages=101)
    assert _read(outcomes) - {_START} == set(stored)

    known_today = set(memory.rows) - {_START}
    assert len(known_today) == 600
    tried: set[str] = set()
    for night in range(3, 9):
        mint(night)
        _results, outcomes, _state = await _night(site, memory, max_pages=101)
        tried |= _read(outcomes)
    assert known_today <= tried


_DOCS, _GUIDE, _ARTICLE = (
    "https://example.com/docs",
    "https://example.com/docs/guide",
    "https://example.com/docs/guide/article",
)
_CHAIN = {_START: [_DOCS], _DOCS: [_GUIDE], _GUIDE: [_ARTICLE]}


@pytest.mark.asyncio
async def test_a_stored_chain_is_discovered_through_its_links(monkeypatch) -> None:
    """Stored pages do not join the frontier, so every link of the chain is followed."""
    site = _Site(live=[_DOCS, _GUIDE, _ARTICLE], sitemap=[], links=_CHAIN)
    site.install(monkeypatch)
    memory = _FakeMemory()

    for _ in range(2):
        results, _outcomes, state = await _night(site, memory, max_depth=3)
        assert {r.url for r in results} == {_START, _DOCS, _GUIDE, _ARTICLE}
        assert state.known_only == set()

    assert {url: row["depth"] for url, row in memory.rows.items()} == {
        _START: 0,
        _DOCS: 1,
        _GUIDE: 2,
        _ARTICLE: 3,
    }


@pytest.mark.asyncio
async def test_a_remembered_address_at_depth_two_has_its_links_followed(monkeypatch) -> None:
    site = _Site(live=[_GUIDE, _ARTICLE], sitemap=[], links={_GUIDE: [_ARTICLE]})
    site.install(monkeypatch)
    memory = _FakeMemory()
    memory.rows[_GUIDE] = {
        "canonical_url": _GUIDE,
        "url": _GUIDE,
        "depth": 2,
        "first_seen_at": 0.0,
        "last_visited_at": 0.0,
    }

    results, _outcomes, state = await _night(site, memory, max_depth=3)

    assert _ARTICLE in {r.url for r in results}
    assert _ARTICLE not in state.known_only


@pytest.mark.asyncio
async def test_an_unlisted_remembered_address_that_answers_404_is_forgotten(
    monkeypatch,
) -> None:
    """(F1) A known-only 404 leaves the memory; a listed 404 stays known."""
    unlisted = _address("unlisted")
    listed = _address("listed")
    site = _Site(live=[], dead=[unlisted, listed], sitemap=[listed])
    site.install(monkeypatch)
    memory = _FakeMemory()
    memory.rows[unlisted] = {
        "canonical_url": unlisted,
        "url": unlisted,
        "depth": 1,
        "first_seen_at": 0.0,
        "last_visited_at": 0.0,
    }

    _results, _outcomes, state = await _night(site, memory)

    assert unlisted in state.forget
    assert unlisted not in memory.rows
    assert listed in memory.rows


@pytest.mark.asyncio
async def test_a_remembered_address_on_the_old_host_is_moved_to_the_crawl_host(
    monkeypatch,
) -> None:
    """A start-URL host change re-keys the memory instead of orphaning it."""
    site = _Site(live=[_address(1)], sitemap=[])
    site.install(monkeypatch)
    memory = _FakeMemory()
    old_key = "https://www.example.com/p/1"
    memory.rows[old_key] = {
        "canonical_url": old_key,
        "url": old_key,
        "depth": 1,
        "first_seen_at": 0.0,
        "last_visited_at": 0.0,
    }

    _results, _outcomes, state = await _night(site, memory)

    assert old_key in state.forget
    assert set(memory.rows) == {_START, _address(1)}


@pytest.mark.asyncio
async def test_a_list_of_only_dead_addresses_ends_at_the_attempt_bound(monkeypatch) -> None:
    """(e) Dead addresses are free against the page limit, but not endless."""
    dead = [_address(i) for i in range(1500)]
    site = _Site(live=[], dead=dead)
    site.install(monkeypatch)

    _results, outcomes, _state = await _night(site, _FakeMemory())

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
    found_tonight = _address("new")
    site = _Site(live=[*addresses, found_tonight], sitemap=[found_tonight, *addresses])
    site.install(monkeypatch)
    monkeypatch.setattr(crawl4ai_client, "_CHECKPOINT_BATCH_SIZE", 2)
    remembered = [
        {
            "canonical_url": url,
            "url": url,
            "depth": 1,
            "first_seen_at": 0.0,
            "last_visited_at": 1.0 if url in addresses[:3] else None,
        }
        for url in addresses
    ]
    # The never-tried addresses seen at 0, then those tried at 1, then what is
    # new tonight.
    expected = [*addresses[3:], *addresses[:3], found_tonight]

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
            start_url=_START,
            max_pages=500,
            checkpoint=checkpoint,
            memory=CrawlMemoryState(remembered=copy.deepcopy(remembered)),
        )
    await crawl4ai_client.crawl_site(
        start_url=_START,
        max_pages=500,
        checkpoint=checkpoint,
        memory=CrawlMemoryState(remembered=copy.deepcopy(remembered)),
    )

    requested = [url for call in site.bulk_calls for url in call]
    assert list(dict.fromkeys(requested)) == expected


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
            "status": "completed",
            "pages_total": 30,
            "pages_done": 30,
            "error": None,
            "error_summary": {"reason": "crawl_frontier_incomplete", "coverage": coverage},
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
            "knowledge_ingest.crawl_memory.PostgresCrawlMemory.record", new_callable=AsyncMock
        ) as record,
    ):
        resp = client.post("/ingest/v1/crawl/preview", json={"url": "https://example.com/docs"})

    assert resp.status_code == 200, resp.text
    load.assert_not_awaited()
    record.assert_not_awaited()


def test_a_page_the_seed_pass_read_counts_as_read() -> None:
    """A read replaces a failure of the same address; nothing replaces a read."""
    outcomes = [
        {"url": _address(1), "reason_code": "timeout"},
        {"url": _address(2), "reason_code": "success"},
        {"url": _address(1), "reason_code": "success"},
        {"url": _address(2), "reason_code": "timeout"},
        {"url": _address(2), "reason_code": "not_fetched_budget_exhausted"},
    ]

    coverage = _crawl_coverage(outcomes, max_pages=500)

    assert coverage["pages_read"] == 2
    assert coverage["addresses_waiting"] == 0
