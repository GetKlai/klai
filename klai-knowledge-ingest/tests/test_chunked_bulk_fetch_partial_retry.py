"""A2 (bulk-path defects block A) — partial success must survive a retry.

Before this fix ``_chunked_bulk_fetch`` kept only the LAST chunk's
transport exception (earlier failures silently discarded), and
``crawl_site``'s stealth-retry step re-sent the WHOLE original batch —
including the URLs whose chunk had already succeeded — throwing away real
results. This locks in the fix: only the failed subset is retried, and
already-succeeded results survive untouched.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import ChunkedFetchResult, _chunked_bulk_fetch
from knowledge_ingest.reason_codes import FetchReasonCode

# One URL per chunk (see test_chunked_bulk_fetch_rate_limit_stop.py for the
# derivation), so N urls produce N distinct HTTP requests we can fail
# independently.
_ONE_URL_PER_CHUNK_RATE_LIMIT = 0.05


def _ok_page(url: str) -> dict[str, Any]:
    return {
        "url": url,
        "success": True,
        "status_code": 200,
        "html": "<html><body>Real content, several words here.</body></html>",
        "markdown": "Real content, several words here.",
        "links": {"internal": []},
        "media": {},
    }


def _opaque_500() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
    response = httpx.Response(
        500, json={"error": "Internal server error", "correlation_id": "abc"}, request=request
    )
    return httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)


# ---------------------------------------------------------------------------
# _chunked_bulk_fetch: multiple failed chunks each keep their own exception.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_differently_failed_chunks_both_keep_their_own_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before this fix, only the LAST chunk's transport_error survived —
    the first chunk's failure (and its distinct exception) vanished."""
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = payload["urls"][0]
        if url == "https://example.com/1":
            raise _opaque_500()
        if url == "https://example.com/2":
            raise httpx.ReadTimeout("simulated timeout")
        return {"results": [_ok_page(url)]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = ["https://example.com/1", "https://example.com/2", "https://example.com/3"]
    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    assert set(fetch.failed) == {"https://example.com/1", "https://example.com/2"}
    assert isinstance(fetch.failed["https://example.com/1"], httpx.HTTPStatusError)
    assert isinstance(fetch.failed["https://example.com/2"], httpx.ReadTimeout)
    # The third chunk succeeded and is untouched.
    assert [p["url"] for p in fetch.raw_results] == ["https://example.com/3"]


# ---------------------------------------------------------------------------
# crawl_site: stealth retry only re-sends the failed subset; the already-
# succeeded chunk's result is preserved, not thrown away and re-fetched.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stealth_retry_only_resends_the_failed_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One chunk fails, one chunk succeeds. The stealth retry must be sent
    for the failed URL only — the succeeded URL must never appear in a
    second (or stealth) bulk request."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return ["https://example.com/ok", "https://example.com/broken"]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> crawl4ai_client.CrawlResult:
        return crawl4ai_client.CrawlResult(
            url=start_url,
            fit_markdown="seed",
            raw_markdown="seed",
            html="<html></html>",
            word_count=1,
            success=True,
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)

    seen_url_sets: list[frozenset[str]] = []

    async def _fake_chunked_bulk_fetch(
        *,
        urls: list[str],
        crawler_config: dict[str, Any],
        cookies: list[dict[str, Any]] | None,
        stealth: bool = False,
        rate_limit: float | None = None,
    ) -> ChunkedFetchResult:
        seen_url_sets.append(frozenset(urls))
        if not stealth:
            # Main attempt: /ok succeeds, /broken fails as its OWN chunk.
            return ChunkedFetchResult(
                raw_results=[_ok_page("https://example.com/ok")],
                failed={"https://example.com/broken": _opaque_500()},
            )
        # Stealth retry — only /broken should ever be requested here.
        assert urls == ["https://example.com/broken"]
        return ChunkedFetchResult(raw_results=[_ok_page("https://example.com/broken")])

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    # Main attempt requested both URLs; the stealth retry requested ONLY
    # the failed one.
    assert seen_url_sets == [
        frozenset({"https://example.com/ok", "https://example.com/broken"}),
        frozenset({"https://example.com/broken"}),
    ]

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/ok"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/broken"] == FetchReasonCode.SUCCESS.value
    result_urls = {r.url for r in results}
    assert "https://example.com/ok" in result_urls
    assert "https://example.com/broken" in result_urls


@pytest.mark.asyncio
async def test_first_attempts_successful_result_survives_into_final_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The specific regression: a URL that succeeded on the FIRST attempt
    must appear in the final results even though a DIFFERENT URL in the
    same batch triggered a stealth retry and sequential recovery."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return ["https://example.com/already-ok", "https://example.com/needs-recovery"]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> crawl4ai_client.CrawlResult:
        return crawl4ai_client.CrawlResult(
            url=start_url,
            fit_markdown="seed",
            raw_markdown="seed",
            html="<html></html>",
            word_count=1,
            success=True,
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)

    async def _fake_chunked_bulk_fetch(
        *,
        urls: list[str],
        crawler_config: dict[str, Any],
        cookies: list[dict[str, Any]] | None,
        stealth: bool = False,
        rate_limit: float | None = None,
    ) -> ChunkedFetchResult:
        if not stealth:
            return ChunkedFetchResult(
                raw_results=[_ok_page("https://example.com/already-ok")],
                failed={"https://example.com/needs-recovery": _opaque_500()},
            )
        # Stealth retry for the failed URL also fails — falls through to
        # sequential recovery.
        assert urls == ["https://example.com/needs-recovery"]
        return ChunkedFetchResult(failed={"https://example.com/needs-recovery": _opaque_500()})

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    async def _fake_recover_bulk_5xx_batch(
        urls: list[str],
        *,
        crawler_config: dict[str, Any],
        cookies: list[dict[str, Any]] | None,
        base_domain: str,
        recovery_budget: int,
        deadline: float | None = None,
        stealth: bool = False,
        trigger_reason_code: str = FetchReasonCode.HTTP_5XX.value,
    ) -> tuple[
        list[crawl4ai_client.CrawlResult],
        list[crawl4ai_client.CrawlResult],
        list[dict[str, Any]],
        int,
    ]:
        # Only the still-failing URL must ever reach sequential recovery —
        # never the whole original batch (the second half of A2's bug).
        assert urls == ["https://example.com/needs-recovery"]
        recovered = crawl4ai_client.CrawlResult(
            url="https://example.com/needs-recovery",
            fit_markdown="recovered",
            raw_markdown="recovered",
            html="<html>recovered</html>",
            word_count=50,
            success=True,
        )
        outcome = {
            "url": "https://example.com/needs-recovery",
            "reason_code": FetchReasonCode.SUCCESS.value,
            "status_code": 200,
            "content_length": 10,
        }
        return [recovered], [recovered], [outcome], 1

    monkeypatch.setattr(crawl4ai_client, "_recover_bulk_5xx_batch", _fake_recover_bulk_5xx_batch)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/already-ok"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/needs-recovery"] == FetchReasonCode.SUCCESS.value
    result_urls = {r.url for r in results}
    assert "https://example.com/already-ok" in result_urls
    assert "https://example.com/needs-recovery" in result_urls


@pytest.mark.asyncio
async def test_not_attempted_urls_never_enter_retry_or_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interaction with A1: URLs skipped because an earlier chunk hit a
    rate-limit signal are NOT "failed" URLs — they must never be sent to
    the stealth retry or the sequential-recovery path."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return ["https://example.com/1", "https://example.com/2"]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> crawl4ai_client.CrawlResult:
        return crawl4ai_client.CrawlResult(
            url=start_url,
            fit_markdown="seed",
            raw_markdown="seed",
            html="<html></html>",
            word_count=1,
            success=True,
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)

    calls: list[dict[str, Any]] = []

    async def _fake_chunked_bulk_fetch(
        *,
        urls: list[str],
        crawler_config: dict[str, Any],
        cookies: list[dict[str, Any]] | None,
        stealth: bool = False,
        rate_limit: float | None = None,
    ) -> ChunkedFetchResult:
        calls.append({"urls": list(urls), "stealth": stealth})
        # Chunk 1 blocked, chunk 2 never attempted — no `failed` entry.
        # BLOCKED_ANTI_BOT (not RATE_LIMITED) so crawl_site stops
        # immediately rather than retrying at a lower rate (Deel B) — this
        # test is about the retry/recovery-path exclusion, not the
        # slowdown-vs-stop decision itself (see
        # tests/test_crawl_rate_limit_slowdown.py for that).
        return ChunkedFetchResult(
            not_attempted=["https://example.com/2"],
            stopped_early=True,
            stop_trigger_reason_code=FetchReasonCode.BLOCKED_ANTI_BOT.value,
        )

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    async def _fail_if_called(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("sequential recovery must never be called for not_attempted URLs")

    monkeypatch.setattr(crawl4ai_client, "_recover_bulk_5xx_batch", _fail_if_called)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    # Only ONE call — no stealth retry, since `failed` was empty.
    assert calls == [{"urls": ["https://example.com/1", "https://example.com/2"], "stealth": False}]

    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url["https://example.com/2"] == FetchReasonCode.NOT_FETCHED_RATE_LIMIT_STOP.value
