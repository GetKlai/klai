"""2026-08-18 support.ascendcloud.com incident — the storing itself.

Four un-rendered-template-syntax links (see
``tests/test_crawl_frontier_template_urls.py`` for the frontier-level fix)
each 404'd with a tiny error body. crawl4ai's structural anti-bot heuristic
misread each one as ``blocked_anti_bot`` (see
``tests/test_crawl_site_reconcile.py::TestClassifyFetchOutcome`` for the
classification-level fix). At the time, BLOCKED_ANTI_BOT unconditionally
stopped chunking, so the FIRST such 404 prevented 192 real pages from being
attempted. Since 2026-08-18 it stops only through the crawl-wide ratio+floor
gate.

This test proves the actual incident is fixed, not just its two
contributing symptoms in isolation: four exact-production-shape 404 pages,
each in its own chunk, none of them may stop the crawl from fetching the
real pages that come after them.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import _chunked_bulk_fetch
from knowledge_ingest.reason_codes import FetchReasonCode

# At 0.05 req/s, the host gate's 10-second window allows one URL per request — one
# URL per chunk, so each URL below produces its own distinct HTTP request
# (matches the pattern in test_chunked_bulk_fetch_rate_limit_stop.py).
_ONE_URL_PER_CHUNK_RATE_LIMIT = 0.05


def _structural_404_page(url: str) -> dict[str, Any]:
    """Exact production shape (support.ascendcloud.com, 2026-08-18): a 404
    for a URL built from un-rendered template syntax, whose tiny error page
    trips crawl4ai's STRUCTURAL anti-bot heuristic."""
    return {
        "url": url,
        "success": False,
        "status_code": 404,
        "error_message": (
            "Blocked by anti-bot protection: Structural: minimal_text "
            "on small page (482 bytes, 42 chars visible)"
        ),
        "html": "",
        "markdown": "",
        "links": {"internal": []},
        "media": {},
    }


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


@pytest.mark.asyncio
async def test_four_structural_404s_do_not_stop_the_remaining_real_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact incident: four bogus template-syntax URLs 404, three real
    pages come after them in the frontier — all three must still be
    fetched."""
    monkeypatch.setattr(crawl4ai_client, "_pacing_monotonic", lambda: 0.0)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(crawl4ai_client, "_pacing_sleep", _no_sleep)

    calls: list[list[str]] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = payload["urls"][0]
        calls.append(list(payload["urls"]))
        if "bogus-template" in url:
            return {"results": [_structural_404_page(url)]}
        return {"results": [_ok_page(url)]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    urls = [
        "https://support.ascendcloud.com/bogus-template-1",
        "https://support.ascendcloud.com/bogus-template-2",
        "https://support.ascendcloud.com/bogus-template-3",
        "https://support.ascendcloud.com/bogus-template-4",
        "https://support.ascendcloud.com/real/page-a",
        "https://support.ascendcloud.com/real/page-b",
        "https://support.ascendcloud.com/real/page-c",
    ]

    fetch = await _chunked_bulk_fetch(
        urls=urls,
        crawler_config={},
        cookies=None,
        rate_limit=_ONE_URL_PER_CHUNK_RATE_LIMIT,
    )

    # Every URL was actually attempted — no chunk was skipped.
    assert calls == [[u] for u in urls]
    assert fetch.stopped_early is False
    assert fetch.not_attempted == []

    reason_by_url = {
        page["url"]: crawl4ai_client._classify_fetch_outcome(page) for page in fetch.raw_results
    }
    for i in range(1, 5):
        assert (
            reason_by_url[f"https://support.ascendcloud.com/bogus-template-{i}"]
            == FetchReasonCode.HTTP_4XX.value
        )
    for suffix in ("a", "b", "c"):
        assert (
            reason_by_url[f"https://support.ascendcloud.com/real/page-{suffix}"]
            == FetchReasonCode.SUCCESS.value
        )
