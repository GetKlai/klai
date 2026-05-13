"""BFS-continuity tests for SPEC-INGEST-LOGIN-WALL-DETECT-001 Phase C.

REQ-04 acceptance criteria:
- AC-04.1: Anonymous wall does NOT halt BFS (unlike SPEC-CRAWLER-004 cookie-path).
- AC-04.2: error_summary populated when walls detected, status=succeeded if >=1 page ingested.
- AC-04.3: status=failed_partial when 0 pages ingested AND >=1 wall skipped.
- AC-04.4: Authenticated halt path (AuthWallDetected) is unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import (
    AnonymousAuthWallDetected,
    run_crawl_job,
)
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.utils.auth_wall_detector import AuthWallSignal


def _result(url: str, *, success: bool = True) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown="content" if success else "",
        raw_markdown="content" if success else "",
        html="<html></html>" if success else "",
        word_count=10 if success else 0,
        success=success,
    )


@pytest.fixture()
def patched_pool():
    """Mock asyncpg connection that records all execute() calls.

    Recorded calls are inspected by tests to verify error_summary writes
    and status transitions.
    """
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    return pool


def _patch_crawler_externals(crawl_results: list[CrawlResult], ingest_side_effect):
    """Helper that returns a context-manager bundle patching all externals
    of run_crawl_job — crawl_site, get_pool, pg_store, _ingest_crawl_result.

    SPEC-INGEST-RECONCILE-001 AC-4: crawl_site now returns the tuple
    ``(results, fetch_outcomes)``. We synthesise an outcomes list from
    the crawl_results so the adapter's JSONB persistence path is
    exercised by the same fixture.
    """
    fetch_outcomes = [
        {
            "url": r.url,
            "reason_code": "success" if r.success else "unknown_exception",
            "status_code": 200 if r.success else None,
            "content_length": len(r.html or ""),
        }
        for r in crawl_results
    ]
    return [
        patch(
            "knowledge_ingest.adapters.crawler.crawl_site",
            new=AsyncMock(return_value=(crawl_results, fetch_outcomes)),
        ),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.upsert_page_links",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new=AsyncMock(side_effect=ingest_side_effect),
        ),
    ]


# ---------------------------------------------------------------------------
# AC-04.1: BFS continues past anonymous wall
# ---------------------------------------------------------------------------


class TestBFSContinuity:
    @pytest.mark.asyncio()
    async def test_anonymous_wall_does_not_halt_bfs(self, patched_pool) -> None:
        """Three pages: clean, walled, clean. Walled raises
        AnonymousAuthWallDetected; the other two MUST still ingest."""
        page_a = _result("https://example.com/a")
        page_b = _result("https://example.com/b")
        page_c = _result("https://example.com/c")

        ingested_urls: list[str] = []

        async def ingest_side_effect(*args, **kwargs):
            url = args[2] if len(args) >= 3 else kwargs.get("url")
            if url == "https://example.com/b":
                raise AnonymousAuthWallDetected(
                    url,
                    AuthWallSignal(
                        pattern="template_cluster",
                        evidence=("have to log in",),
                        confidence=0.9,
                    ),
                )
            ingested_urls.append(url)

        patches = _patch_crawler_externals([page_a, page_b, page_c], ingest_side_effect)
        for p in patches:
            p.start()
        try:
            await run_crawl_job(
                conn=patched_pool,
                job_id="job-bfs-1",
                org_id="368884765035593759",
                kb_slug="support",
                start_url="https://example.com",
                login_indicator_selector=None,  # anonymous crawl
            )
        finally:
            for p in patches:
                p.stop()

        # Both clean pages ingested.
        assert ingested_urls == ["https://example.com/a", "https://example.com/c"]


# ---------------------------------------------------------------------------
# AC-04.2: error_summary populated, status=succeeded
# ---------------------------------------------------------------------------


def _find_error_summary_call(execute_mock):
    """Locate the UPDATE that wrote error_summary, if any."""
    for call in execute_mock.await_args_list:
        if not call.args:
            continue
        sql = call.args[0] if isinstance(call.args[0], str) else ""
        if "error_summary" in sql:
            return call
    return None


def _find_status_calls(execute_mock):
    """Return all UPDATE-status calls in chronological order."""
    out = []
    for call in execute_mock.await_args_list:
        if not call.args:
            continue
        sql = call.args[0] if isinstance(call.args[0], str) else ""
        if "UPDATE knowledge.crawl_jobs" in sql and "status" in sql:
            out.append(call)
    return out


class TestErrorSummaryWritten:
    @pytest.mark.asyncio()
    async def test_walls_detected_writes_error_summary(self, patched_pool) -> None:
        """One clean + three walls → error_summary contains login_walls_skipped=3."""
        clean = _result("https://example.com/clean")
        walls = [_result(f"https://example.com/wall-{i}") for i in range(3)]

        async def ingest_side_effect(*args, **kwargs):
            url = args[2] if len(args) >= 3 else kwargs.get("url")
            if "wall" in url:
                raise AnonymousAuthWallDetected(
                    url,
                    AuthWallSignal(pattern="template_cluster", confidence=0.9),
                )

        patches = _patch_crawler_externals([clean, *walls], ingest_side_effect)
        for p in patches:
            p.start()
        try:
            await run_crawl_job(
                conn=patched_pool,
                job_id="job-summary-1",
                org_id="368884765035593759",
                kb_slug="support",
                start_url="https://example.com",
                login_indicator_selector=None,
            )
        finally:
            for p in patches:
                p.stop()

        # error_summary UPDATE was issued.
        summary_call = _find_error_summary_call(patched_pool.execute)
        assert summary_call is not None, (
            "expected an UPDATE that writes error_summary; got "
            f"{patched_pool.execute.await_args_list}"
        )

        # The summary itself should contain the count + sample URLs.
        # We pass the JSON as a Python dict via json.dumps in the producer;
        # locate it in the call args and assert shape.
        import json

        json_arg = next(
            (a for a in summary_call.args if isinstance(a, str) and "login_walls_skipped" in a),
            None,
        )
        assert json_arg is not None, (
            f"expected a JSON arg containing login_walls_skipped; got {summary_call.args}"
        )
        parsed = json.loads(json_arg)
        assert parsed["login_walls_skipped"] == 3
        assert len(parsed["sample_urls"]) == 3
        assert all("wall" in u for u in parsed["sample_urls"])


# ---------------------------------------------------------------------------
# AC-04.3: failed_partial when 0 pages ingested
# ---------------------------------------------------------------------------


class TestFailedPartial:
    @pytest.mark.asyncio()
    async def test_zero_ingested_with_walls_marks_failed_partial(self, patched_pool) -> None:
        walls = [_result(f"https://example.com/wall-{i}") for i in range(3)]

        async def ingest_side_effect(*args, **kwargs):
            url = args[2] if len(args) >= 3 else kwargs.get("url")
            raise AnonymousAuthWallDetected(
                url,
                AuthWallSignal(pattern="template_cluster", confidence=0.9),
            )

        patches = _patch_crawler_externals(walls, ingest_side_effect)
        for p in patches:
            p.start()
        try:
            await run_crawl_job(
                conn=patched_pool,
                job_id="job-fp-1",
                org_id="368884765035593759",
                kb_slug="support",
                start_url="https://example.com",
                login_indicator_selector=None,
            )
        finally:
            for p in patches:
                p.stop()

        status_calls = _find_status_calls(patched_pool.execute)
        # The terminal status update must be 'failed_partial'.
        terminal = status_calls[-1]
        assert "failed_partial" in [a for a in terminal.args if isinstance(a, str)], (
            f"expected terminal status 'failed_partial'; got {terminal.args}"
        )

    @pytest.mark.asyncio()
    async def test_some_ingested_succeeds_despite_walls(self, patched_pool) -> None:
        # SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-4: when wall ratio exceeds
        # KLAI_INGEST_AUTHWALL_DIRTY_TRIP_RATE (default 0.30) and no cookies /
        # login_indicator are configured, the run is intentionally marked
        # failed_partial. Below the threshold, the existing "some content got
        # through → completed" semantic still holds.
        # Ratio here: 1 wall / 9 total = 11% — comfortably below default 30%.
        clean_pages = [_result(f"https://example.com/clean-{i}") for i in range(8)]
        walls = [_result("https://example.com/wall-0")]

        async def ingest_side_effect(*args, **kwargs):
            url = args[2] if len(args) >= 3 else kwargs.get("url")
            if "wall" in url:
                raise AnonymousAuthWallDetected(
                    url,
                    AuthWallSignal(pattern="template_cluster", confidence=0.9),
                )

        patches = _patch_crawler_externals([*clean_pages, *walls], ingest_side_effect)
        for p in patches:
            p.start()
        try:
            await run_crawl_job(
                conn=patched_pool,
                job_id="job-mixed-1",
                org_id="368884765035593759",
                kb_slug="support",
                start_url="https://example.com",
                login_indicator_selector=None,
            )
        finally:
            for p in patches:
                p.stop()

        status_calls = _find_status_calls(patched_pool.execute)
        terminal_status_args = [a for a in status_calls[-1].args if isinstance(a, str)]
        # 'completed' is the legacy success alias, retained for compat.
        assert any(v in terminal_status_args for v in ("succeeded", "completed")), (
            f"expected success status; got {status_calls[-1].args}"
        )
        assert "failed_partial" not in terminal_status_args


# ---------------------------------------------------------------------------
# AC-04.4: existing AuthWallDetected behaviour unchanged
# ---------------------------------------------------------------------------


class TestAuthenticatedHaltUnchanged:
    @pytest.mark.asyncio()
    async def test_authwall_detected_still_halts_bfs(self, patched_pool) -> None:
        """When login_indicator_selector is set and crawl4ai returns
        success=False, AuthWallDetected must STILL halt BFS — the
        SPEC-CRAWLER-004 contract is preserved."""
        clean = _result("https://example.com/clean")
        walled_failed = _result("https://example.com/walled", success=False)
        unreached = _result("https://example.com/unreached")

        ingested_urls: list[str] = []

        async def ingest_side_effect(*args, **kwargs):
            url = args[2] if len(args) >= 3 else kwargs.get("url")
            ingested_urls.append(url)

        patches = _patch_crawler_externals([clean, walled_failed, unreached], ingest_side_effect)
        for p in patches:
            p.start()
        try:
            await run_crawl_job(
                conn=patched_pool,
                job_id="job-authwall-1",
                org_id="368884765035593759",
                kb_slug="support",
                start_url="https://example.com",
                login_indicator_selector="#login-form",  # cookie path
            )
        finally:
            for p in patches:
                p.stop()

        # Only the first clean page ingested; walled aborts BFS, unreached
        # never reached.
        assert ingested_urls == ["https://example.com/clean"]

        status_calls = _find_status_calls(patched_pool.execute)
        terminal_args = [a for a in status_calls[-1].args if isinstance(a, str)]
        assert "failed" in terminal_args, (
            f"expected terminal status 'failed' from AuthWallDetected; got {status_calls[-1].args}"
        )
        # The failed reason must mention auth_wall_detected (the original
        # selector-based message).
        assert any("auth_wall_detected" in a for a in terminal_args), (
            f"expected error containing auth_wall_detected; got {terminal_args}"
        )
        # And it must NOT have written error_summary (different code path).
        assert _find_error_summary_call(patched_pool.execute) is None
