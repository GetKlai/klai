"""Decision: when does a finished remote crawl count as a succeeded sync."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.crawl_sync_status import remote_crawl_coverage, remote_crawl_succeeded


def _live(status: str, *, error: str | None = None, **coverage: Any) -> dict[str, Any]:
    live: dict[str, Any] = {"status": status, "error": error, "pages_done": 80, "pages_total": 80}
    if coverage:
        live["coverage"] = {
            "pages_read": 80,
            "addresses_gone": 0,
            "addresses_waiting": 0,
            "addresses_known": 80,
            "max_pages": 80,
            "stopped_by": None,
            **coverage,
        }
    return live


@pytest.mark.parametrize("stopped_by", ["page_limit", "attempt_limit", "site_rate_limit"])
def test_crawl_that_read_pages_and_left_addresses_for_later_succeeded(stopped_by: str) -> None:
    live = _live("failed", error="crawl_budget_exhausted", stopped_by=stopped_by, addresses_waiting=40)

    assert remote_crawl_succeeded(live) is True
    assert remote_crawl_coverage(live) == live["coverage"]


@pytest.mark.parametrize(
    "live",
    [
        _live("failed", error="blocked", stopped_by="blocked"),
        _live("failed", error="crawl_budget_exhausted", stopped_by="page_limit", pages_read=0),
        _live("failed", error="cancelled", stopped_by="cancelled"),
        _live("failed", error="login_required:no_cookies_for:site.example.org", stopped_by="page_limit"),
        _live("failed", error="internal_crash"),
    ],
)
def test_other_failed_crawls_stay_failed(live: dict[str, Any]) -> None:
    assert remote_crawl_succeeded(live) is False


def test_without_coverage_nothing_changes() -> None:
    assert remote_crawl_succeeded(_live("completed")) is True
    assert remote_crawl_succeeded(_live("failed", error="crawl_budget_exhausted")) is False
    assert remote_crawl_coverage(_live("failed")) is None
