"""fix/thin-recovery-rate-limit-passthrough — ``_recover_thin_bulk_results``
must forward ``rate_limit`` to its own ``_chunked_bulk_fetch`` call.

Regression coverage for a gap found 2026-08-18: ``crawl_site``'s main batch
fetch and its stealth retry both pass ``rate_limit=rate_limit`` through to
``_chunked_bulk_fetch`` (client-side pacing, fix/client-side-crawl-pacing),
but the thin-content relaxed-retry fetch inside ``_recover_thin_bulk_results``
did not — a rate-limited domain got an unpaced burst on every batch that
produced thin results, defeating the pacing this whole mechanism exists for.
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge_ingest import crawl4ai_client


@pytest.mark.asyncio
async def test_recover_thin_bulk_results_forwards_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_rate_limits: list[float | None] = []

    async def _fake_chunked_bulk_fetch(
        *,
        urls: list[str],
        crawler_config: dict[str, Any],
        cookies: list[dict[str, Any]] | None,
        stealth: bool = False,
        rate_limit: float | None = None,
    ) -> tuple[list[dict[str, Any]], BaseException | None]:
        seen_rate_limits.append(rate_limit)
        return (
            [
                {
                    "url": u,
                    "success": True,
                    "status_code": 200,
                    "html": "<html><body>Relaxed content, plenty of words here now.</body></html>",
                    "markdown": "Relaxed content, plenty of words here now.",
                    "links": {"internal": []},
                    "media": {},
                }
                for u in urls
            ],
            None,
        )

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_chunked_bulk_fetch)

    # A thin result: word_count below threshold but HTML clearly holds
    # content — this is what `_should_retry_relaxed_for_thin_content` selects.
    thin_result = crawl4ai_client.CrawlResult(
        url="https://example.com/thin-page",
        fit_markdown="",
        raw_markdown="short",
        html="<html><body>" + ("word " * 100) + "</body></html>",
        word_count=1,
        success=True,
    )

    await crawl4ai_client._recover_thin_bulk_results(
        [thin_result],
        crawler_config={},
        cookies=None,
        base_domain="example.com",
        rate_limit=0.5,
    )

    assert seen_rate_limits == [0.5], (
        "_recover_thin_bulk_results must forward rate_limit to its own "
        "_chunked_bulk_fetch call — an unrelated pacing gap lets a rate-limited "
        "domain get an unpaced burst whenever a batch has thin results."
    )
