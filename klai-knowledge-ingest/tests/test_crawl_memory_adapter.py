"""How ``run_crawl_job`` records the crawl memory and decides the job's status.

``crawl_site`` is replaced by a fake that answers like a crawl and fills the
memory handoff; the memory itself is a recording fake. What is pinned:

- The memory is recorded inside the guarded completion, only for a run whose
  pages were accepted; a superseded attempt records nothing, and a failed
  record fails the job after the pages are in.
- A complete crawl forgets addresses it only knew from memory, and a stored
  page at such an address is stale exactly as before the memory existed.
- A crawl that read pages and left addresses waiting because of the page
  limit, the attempt bound or the site's rate limit ends ``completed``; a
  blocked crawl ends ``failed_partial``.
"""

from __future__ import annotations

import json
from contextlib import ExitStack, asynccontextmanager
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.crawl_checkpoint import CrawlExecutionSuperseded
from tests.conftest import connection_factory_for

_START = "https://example.com/"


def _url(name: str) -> str:
    return f"https://example.com/p/{name}"


def _result(url: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=f"Body of {url}",
        raw_markdown=f"Body of {url}",
        html="<html></html>",
        word_count=3,
        success=True,
    )


def _outcome(url: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"url": url, "reason_code": reason, "status_code": None, **extra}


class _Memory:
    instances: ClassVar[list[_Memory]] = []

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.recorded: list[tuple[list[dict[str, Any]], list[str]]] = []
        self.fail = False
        _Memory.instances.append(self)

    async def load(self) -> list[dict[str, Any]]:
        return []

    async def record(self, _conn: Any, rows: list[dict[str, Any]], forget: list[str]) -> None:
        if self.fail:
            raise RuntimeError("memory write failed")
        self.recorded.append((rows, forget))


def _fake_crawl(
    results: list[CrawlResult],
    outcomes: list[dict[str, Any]],
    *,
    rows: list[dict[str, Any]] | None = None,
    forget: list[str] | None = None,
    known_only: set[str] | None = None,
):
    async def _crawl_site(**kwargs: Any) -> tuple[list[CrawlResult], list[dict[str, Any]]]:
        state = kwargs.get("memory")
        if state is not None:
            state.rows = rows or []
            state.forget = forget or []
            state.known_only = known_only or set()
        return list(results), list(outcomes)

    return _crawl_site


def _row(url: str, *, known_only: bool = False) -> dict[str, Any]:
    return {
        "canonical_url": url,
        "url": url,
        "depth": 1,
        "visited": True,
        "reason_code": "success",
        "known_only": known_only,
    }


async def _run(
    crawl: Any,
    *,
    judge: tuple[bool, dict | None] = (False, None),
    guard: Any = None,
    memory_fails: bool = False,
    stale: list[str] | None = None,
) -> dict[str, Any]:
    _Memory.instances = []
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    pg = MagicMock()
    pg.get_crawled_page_hashes = AsyncMock(return_value={})
    pg.get_crawled_pages_markdown = AsyncMock(return_value=[])
    pg.list_stale_connector_artifact_paths = AsyncMock(return_value=stale or [])
    finished = AsyncMock()
    ingest = AsyncMock(return_value=False)

    class _FailingMemory(_Memory):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.fail = memory_fails

    patches = [
        patch("knowledge_ingest.adapters.crawler.pg_store", pg),
        patch("knowledge_ingest.adapters.crawler.PostgresCrawlMemory", _FailingMemory),
        patch("knowledge_ingest.adapters.crawler.crawl_site", crawl),
        patch(
            "knowledge_ingest.adapters.crawler._judge_run_before_write",
            new_callable=AsyncMock,
            return_value=judge,
        ),
        patch("knowledge_ingest.adapters.crawler._ingest_crawl_result", ingest),
        patch(
            "knowledge_ingest.adapters.crawler._enqueue_taxonomy_backfill_after_crawl",
            new_callable=AsyncMock,
        ),
        patch("knowledge_ingest.adapters.crawler._build_link_graph", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.adapters.crawler._purge_stale_paths",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("knowledge_ingest.adapters.crawler.finish_crawl_execution", finished),
    ]
    if guard is not None:
        patches.append(patch("knowledge_ingest.adapters.crawler.guard_crawl_execution", guard))
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
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
    return {
        "memory": _Memory.instances[0],
        "finished": finished,
        "ingest": ingest,
        "stale": pg.list_stale_connector_artifact_paths,
        "conn": conn,
    }


def _finished_status(run: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    kwargs = run["finished"].await_args.kwargs
    return kwargs["status"], json.loads(kwargs["error_summary"] or "{}")


@pytest.mark.asyncio
async def test_a_run_judged_write_nothing_leaves_the_memory_untouched() -> None:
    """The accept-regression rerun then crawls the same addresses again."""
    crawl = _fake_crawl([_result(_START)], [_outcome(_START, "success")], rows=[_row(_START)])
    run = await _run(crawl, judge=(True, {"reason": "content_regression"}))

    assert run["memory"].recorded == []
    assert _finished_status(run)[0] == "failed_partial"


@pytest.mark.asyncio
async def test_an_accepted_run_records_after_its_pages_are_written() -> None:
    crawl = _fake_crawl([_result(_START)], [_outcome(_START, "success")], rows=[_row(_START)])
    run = await _run(crawl)

    assert run["ingest"].await_count == 1
    assert run["memory"].recorded == [([_row(_START)], [])]


@pytest.mark.asyncio
async def test_a_superseded_execution_does_not_record() -> None:
    uses = 0

    @asynccontextmanager
    async def _guard(_conn: Any, _job_id: str, _generation: int):
        nonlocal uses
        uses += 1
        # The completion block is the third guarded block: outcomes, one page,
        # completion.
        if uses == 3:
            raise CrawlExecutionSuperseded("job-1")
        yield

    crawl = _fake_crawl([_result(_START)], [_outcome(_START, "success")], rows=[_row(_START)])
    run = await _run(crawl, guard=_guard)

    assert run["memory"].recorded == []
    run["finished"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_record_fails_the_job_after_the_pages_are_in() -> None:
    crawl = _fake_crawl([_result(_START)], [_outcome(_START, "success")], rows=[_row(_START)])
    run = await _run(crawl, memory_fails=True)

    assert run["ingest"].await_count == 1
    statuses = [c.kwargs["status"] for c in run["finished"].await_args_list]
    assert statuses == ["failed"]


@pytest.mark.asyncio
async def test_a_complete_crawl_forgets_unlisted_addresses_and_retires_their_pages() -> None:
    """(F2) A page dropped from the site's navigation but still answering 200 is stale."""
    unlisted = _url("unlisted")
    crawl = _fake_crawl(
        [_result(_START), _result(unlisted)],
        [_outcome(_START, "success"), _outcome(unlisted, "success")],
        rows=[_row(_START), _row(unlisted, known_only=True)],
        known_only={unlisted},
    )
    run = await _run(crawl, stale=[unlisted])

    assert run["stale"].await_args.kwargs["current_paths"] == [_START]
    rows, forget = run["memory"].recorded[0]
    assert forget == [unlisted]
    assert [r["canonical_url"] for r in rows] == [_START]


@pytest.mark.asyncio
async def test_an_incomplete_crawl_keeps_unlisted_addresses() -> None:
    unlisted = _url("unlisted")
    crawl = _fake_crawl(
        [_result(_START), _result(unlisted)],
        [
            _outcome(_START, "success"),
            _outcome(unlisted, "success"),
            _outcome(_url("later"), "not_fetched_budget_exhausted"),
        ],
        rows=[_row(_START), _row(unlisted, known_only=True)],
        known_only={unlisted},
    )
    run = await _run(crawl)

    rows, forget = run["memory"].recorded[0]
    assert forget == []
    assert unlisted in [r["canonical_url"] for r in rows]


_PAGES = [_outcome(_START, "success"), _outcome(_url("a"), "success")]
_WAITING = [_url("w1"), _url("w2"), _url("w3")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcomes", "status", "stopped_by"),
    [
        (
            [*_PAGES, *[_outcome(u, "not_fetched_budget_exhausted") for u in _WAITING]],
            "completed",
            "page_limit",
        ),
        (
            [
                *_PAGES,
                _outcome(_url("dead"), "gone"),
                *[_outcome(u, "not_fetched_budget_exhausted", budget="attempts") for u in _WAITING],
            ],
            "completed",
            "attempt_limit",
        ),
        (
            [
                *_PAGES,
                _outcome(_url("slow"), "rate_limited"),
                *[_outcome(u, "not_fetched_rate_limit_stop") for u in _WAITING],
            ],
            "completed",
            "site_rate_limit",
        ),
        (
            [
                *_PAGES,
                _outcome(_url("wall"), "blocked_anti_bot"),
                *[_outcome(u, "not_fetched_rate_limit_stop") for u in _WAITING],
            ],
            "failed_partial",
            "blocked",
        ),
        (
            [
                *_PAGES,
                *[_outcome(u, "not_fetched_circuit_breaker_stop") for u in _WAITING],
            ],
            "failed_partial",
            "blocked",
        ),
    ],
    ids=["page_limit", "attempt_limit", "site_rate_limit", "blocked", "breaker"],
)
async def test_a_rotating_crawl_ends_completed_and_a_blocked_one_does_not(
    outcomes: list[dict[str, Any]], status: str, stopped_by: str
) -> None:
    results = [_result(_START), _result(_url("a"))]
    run = await _run(_fake_crawl(results, outcomes))

    final_status, summary = _finished_status(run)
    assert final_status == status
    assert summary["reason"]
    assert summary["coverage"]["stopped_by"] == stopped_by
    assert summary["coverage"]["pages_read"] == 2
    assert summary["coverage"]["addresses_waiting"] == 3


@pytest.mark.asyncio
async def test_a_clean_crawl_summary_carries_coverage() -> None:
    """(g) A crawl with nothing to warn about still reports how far it got."""
    crawl = _fake_crawl(
        [_result(_START)],
        [_outcome(_START, "success"), _outcome(_url("1"), "gone")],
    )
    run = await _run(crawl)

    status, summary = _finished_status(run)
    assert status == "completed"
    assert summary == {
        "coverage": {
            "pages_read": 1,
            "addresses_gone": 1,
            "addresses_waiting": 0,
            "addresses_known": 2,
            "max_pages": 500,
            "stopped_by": None,
        }
    }
