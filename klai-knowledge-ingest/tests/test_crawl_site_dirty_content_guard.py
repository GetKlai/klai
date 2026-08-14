"""Tests for REQ-4 sync-time dirty-content guard.

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-4 / REQ-6.

The guard is a pure decision function — given the post-fetch counters
plus the connector configuration (cookies / login_indicator), it returns
whether the crawl_job should end with ``failed_partial`` plus a
structured ``error_summary`` payload.

Pure unit tests are sufficient — the integration into ``run_crawl_job``
is a single call-site that constructs the inputs from existing local
variables.
"""

from __future__ import annotations

import pytest

from knowledge_ingest.adapters.crawler import (
    ANTIBOT_BLOCKED_REASON,
    CRAWL_BUDGET_EXHAUSTED_REASON,
    CRAWL_FETCH_FAILED_REASON,
    DIRTY_CONTENT_REASON,
    _build_crawl_outcome_warning,
    _crawl_warning_terminal_status,
    decide_antibot_terminal_status,
    decide_terminal_status,
)


def test_dirty_content_above_threshold_marks_failed_partial() -> None:
    """REQ-4: 70% trip-rate, no cookies, no indicator → failed_partial."""
    status, summary = decide_terminal_status(
        auth_wall_count=70,
        total_count=100,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert status == "failed_partial"
    assert summary is not None
    assert summary["reason"] == DIRTY_CONTENT_REASON
    assert summary["reason"] == "boilerplate_or_authwall_dominant"
    assert summary["trip_rate"] == 0.7
    assert summary["auth_wall_count"] == 70
    assert summary["total_count"] == 100
    assert "Re-run preview" in summary["suggestion"]


def test_dirty_content_above_threshold_with_cookies_does_not_trip_guard() -> None:
    """Operator configured cookies — they EXPECTED auth-walled content. The
    REQ-4 guard MUST NOT fire. Existing per-page wall handling still applies."""
    status, summary = decide_terminal_status(
        auth_wall_count=70,
        total_count=100,
        has_cookies=True,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert status != "failed_partial" or (summary or {}).get("reason") != DIRTY_CONTENT_REASON


def test_dirty_content_above_threshold_with_login_indicator_does_not_trip_guard() -> None:
    status, summary = decide_terminal_status(
        auth_wall_count=70,
        total_count=100,
        has_cookies=False,
        has_login_indicator=True,
        threshold=0.30,
    )
    assert status != "failed_partial" or (summary or {}).get("reason") != DIRTY_CONTENT_REASON


def test_dirty_content_below_threshold_succeeds() -> None:
    status, summary = decide_terminal_status(
        auth_wall_count=10,
        total_count=100,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert status != "failed_partial" or (summary or {}).get("reason") != DIRTY_CONTENT_REASON


def test_threshold_inclusive_at_exactly_thirty_percent() -> None:
    """EC-4: trip_rate exactly 0.30 → guard MUST trip (>= is inclusive)."""
    status, summary = decide_terminal_status(
        auth_wall_count=30,
        total_count=100,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert status == "failed_partial"
    assert summary is not None
    assert summary["reason"] == DIRTY_CONTENT_REASON


def test_threshold_configurable_at_fifty_percent() -> None:
    """EC-5: env-var raises threshold to 0.50, 40% trip-rate must NOT trip."""
    status, summary = decide_terminal_status(
        auth_wall_count=40,
        total_count=100,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.50,
    )
    assert status != "failed_partial" or (summary or {}).get("reason") != DIRTY_CONTENT_REASON


def test_zero_total_does_not_divide_by_zero() -> None:
    """Defensive: empty crawl (no candidates) → no guard trip, no exception."""
    status, summary = decide_terminal_status(
        auth_wall_count=0,
        total_count=0,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert status != "failed_partial" or (summary or {}).get("reason") != DIRTY_CONTENT_REASON


def test_summary_contains_actionable_suggestion() -> None:
    """REQ-5 hooks into this exact text — pin the wording."""
    _, summary = decide_terminal_status(
        auth_wall_count=70,
        total_count=100,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert summary is not None
    suggestion = summary["suggestion"]
    # Must reference both possible operator actions: re-run preview AND
    # acknowledge the source may have changed.
    assert "preview" in suggestion.lower()
    assert "authentication" in suggestion.lower() or "content_selector" in suggestion.lower()


def test_budget_exhausted_outcomes_build_failed_partial_warning() -> None:
    warning = _build_crawl_outcome_warning(
        [
            {
                "url": "https://example.com/fetched",
                "reason_code": "success",
                "status_code": 200,
                "content_length": 100,
            },
            {
                "url": "https://example.com/not-fetched",
                "reason_code": "not_fetched_budget_exhausted",
                "status_code": None,
                "content_length": 0,
            },
        ],
        max_pages=200,
    )

    assert warning is not None
    assert warning["reason"] == CRAWL_BUDGET_EXHAUSTED_REASON
    assert warning["omitted_count"] == 1
    assert warning["omitted_reason_counts"] == {"not_fetched_budget_exhausted": 1}
    assert warning["sample_omitted_urls"] == ["https://example.com/not-fetched"]
    assert _crawl_warning_terminal_status(warning) == "failed_partial"


def test_all_failed_outcomes_build_failed_partial_warning() -> None:
    warning = _build_crawl_outcome_warning(
        [
            {
                "url": "https://example.com/",
                "reason_code": "unknown_exception",
                "status_code": None,
                "content_length": 0,
            },
        ],
        max_pages=500,
    )

    assert warning is not None
    assert warning["reason"] == CRAWL_FETCH_FAILED_REASON
    assert warning["failed_count"] == 1
    assert warning["failed_reason_counts"] == {"unknown_exception": 1}
    assert warning["sample_failed_urls"] == ["https://example.com/"]
    assert _crawl_warning_terminal_status(warning) == "failed_partial"


@pytest.mark.parametrize("trip_rate_input", [0.31, 0.5, 0.7, 1.0])
def test_trip_rate_rounded_to_three_decimals(trip_rate_input: float) -> None:
    auth_wall_count = int(trip_rate_input * 100)
    _, summary = decide_terminal_status(
        auth_wall_count=auth_wall_count,
        total_count=100,
        has_cookies=False,
        has_login_indicator=False,
        threshold=0.30,
    )
    assert summary is not None
    # Must be a finite float, not a Decimal or numpy type.
    assert isinstance(summary["trip_rate"], float)
    # Must round to 3 decimals or fewer for clean log output.
    rounded = round(summary["trip_rate"], 3)
    assert summary["trip_rate"] == rounded


# ---------------------------------------------------------------------------
# decide_antibot_terminal_status — 2026-08-14 anti-bot guard, sibling of the
# REQ-4 auth-wall guard above (intermedia.com incident).
# ---------------------------------------------------------------------------


def test_antibot_above_threshold_marks_failed_partial() -> None:
    """16 of 18 discovered pages still BLOCKED_ANTI_BOT after sequential
    recovery (intermedia.com shape) → failed_partial with the anti-bot
    reason, not a silent 'completed'."""
    status, summary = decide_antibot_terminal_status(
        blocked_count=16,
        total_count=18,
        threshold=0.30,
    )
    assert status == "failed_partial"
    assert summary is not None
    assert summary["reason"] == ANTIBOT_BLOCKED_REASON
    assert summary["reason"] == "blocked_by_anti_bot"
    assert summary["blocked_count"] == 16
    assert summary["total_count"] == 18
    assert summary["trip_rate"] == round(16 / 18, 3)
    assert "anti-bot" in summary["suggestion"].lower()


def test_antibot_below_threshold_does_not_trip_guard() -> None:
    """Only a couple of pages still blocked after recovery — not a
    meaningful fraction of the site — guard must not fire."""
    status, summary = decide_antibot_terminal_status(
        blocked_count=2,
        total_count=100,
        threshold=0.30,
    )
    assert status == ""
    assert summary is None


def test_antibot_zero_blocked_does_not_trip_guard() -> None:
    """Recovery cleared every blocked URL — nothing to report."""
    status, summary = decide_antibot_terminal_status(
        blocked_count=0,
        total_count=100,
        threshold=0.30,
    )
    assert status == ""
    assert summary is None


def test_antibot_zero_total_does_not_trip_guard() -> None:
    """No discovered candidates at all — division-by-zero guard."""
    status, summary = decide_antibot_terminal_status(
        blocked_count=0,
        total_count=0,
        threshold=0.30,
    )
    assert status == ""
    assert summary is None


def test_antibot_threshold_inclusive_at_exactly_thirty_percent() -> None:
    """trip_rate exactly 0.30 → guard MUST trip (>= is inclusive, mirrors
    the auth-wall guard's inclusive threshold contract)."""
    status, summary = decide_antibot_terminal_status(
        blocked_count=30,
        total_count=100,
        threshold=0.30,
    )
    assert status == "failed_partial"
    assert summary is not None
    assert summary["reason"] == ANTIBOT_BLOCKED_REASON
