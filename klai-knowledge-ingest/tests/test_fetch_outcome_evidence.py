"""Tests for fetch-outcome failure evidence (Deel A).

Production has ~1119 failed fetches (unknown_exception + timeout, all-time)
whose ``crawl_jobs.fetch_outcomes`` entries carry nothing beyond
``{"url", "reason_code", "status_code", "content_length"}`` — no exception
type, no error message, no correlation_id. These tests pin the additive
evidence fields (``error_type``, ``error_message``, ``correlation_id``,
``observed``) that let an ``unknown_exception`` outcome be diagnosed instead
of being a dead end.
"""

from __future__ import annotations

import httpx

from knowledge_ingest.crawl4ai_client import (
    _ERROR_MESSAGE_MAX_LEN,
    CrawlResult,
    _build_outcome_from_result,
    _error_type_name,
    _extract_correlation_id,
    _mask_sensitive_query_params,
    _outcome_for_failed_url,
    _outcome_for_not_attempted_url,
    _truncate_error_message,
)


def _http_status_error(*, status: int = 500, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
    response = httpx.Response(status, json=body or {}, request=request)
    return httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)


# ---------------------------------------------------------------------------
# _outcome_for_failed_url — a fetch that actually raised an exception
# ---------------------------------------------------------------------------


class TestOutcomeForFailedUrlPreservesExceptionEvidence:
    def test_read_timeout_preserves_exception_type_and_truncated_message(self) -> None:
        error = httpx.ReadTimeout("simulated read timeout after 90s")
        outcome = _outcome_for_failed_url("https://example.com/a", error)

        assert outcome["error_type"] == "httpx.ReadTimeout"
        assert outcome["error_message"] == "simulated read timeout after 90s"
        assert outcome["correlation_id"] is None
        assert outcome["observed"] is True

    def test_5xx_with_correlation_id_body_preserves_correlation_id(self) -> None:
        """2026-08-14 intermedia.com shape: opaque 500 body carrying only
        an 'error' string and a correlation_id — the only handle to cross-
        reference the failure against crawl4ai's own logs."""
        error = _http_status_error(
            status=500,
            body={"error": "Internal server error", "correlation_id": "188834187d7d"},
        )
        outcome = _outcome_for_failed_url("https://example.com/a", error)

        assert outcome["error_type"] == "httpx.HTTPStatusError"
        assert outcome["correlation_id"] == "188834187d7d"
        assert outcome["error_message"] is not None
        assert "188834187d7d" in outcome["error_message"]
        assert outcome["observed"] is True

    def test_connection_error_preserves_exception_type(self) -> None:
        error = httpx.ConnectError("Connection refused")
        outcome = _outcome_for_failed_url("https://example.com/a", error)

        assert outcome["error_type"] == "httpx.ConnectError"
        assert outcome["observed"] is True


# ---------------------------------------------------------------------------
# _outcome_for_not_attempted_url — a URL we deliberately never sent
# ---------------------------------------------------------------------------


class TestOutcomeForNotAttemptedUrlIsDerivedNotObserved:
    def test_not_attempted_url_is_marked_derived(self) -> None:
        outcome = _outcome_for_not_attempted_url("https://example.com/never-sent")

        assert outcome["observed"] is False
        assert outcome["error_type"] is None
        assert outcome["error_message"] is None
        assert outcome["correlation_id"] is None


# ---------------------------------------------------------------------------
# _build_outcome_from_result — the seed-page path
# ---------------------------------------------------------------------------


class TestBuildOutcomeFromResultSeedPath:
    def test_successful_seed_fetch_is_observed_with_no_evidence(self) -> None:
        result = CrawlResult(
            url="https://example.com",
            fit_markdown="hello",
            raw_markdown="hello",
            html="<html>hello</html>",
            word_count=1,
            success=True,
        )
        outcome = _build_outcome_from_result("https://example.com", result)

        assert outcome["observed"] is True
        assert outcome["error_type"] is None
        assert outcome["error_message"] is None
        assert outcome["correlation_id"] is None

    def test_seed_fetch_exception_preserves_type_and_correlation_id(self) -> None:
        """The seed path (_fetch_seed_page) catches the raised exception and
        collapses it into CrawlResult.error_message (a bare str(exc)) — but
        also now carries the raw exception type + body on
        CrawlResult.error_type / .raw_error_text so the evidence survives."""
        result = CrawlResult(
            url="https://example.com",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="crawl4ai failed",
            error_type="httpx.HTTPStatusError",
            raw_error_text='{"error": "Internal server error", "correlation_id": "abc123"}',
        )
        outcome = _build_outcome_from_result("https://example.com", result)

        assert outcome["observed"] is True
        assert outcome["error_type"] == "httpx.HTTPStatusError"
        assert outcome["correlation_id"] == "abc123"
        assert "abc123" in outcome["error_message"]

    def test_seed_fetch_page_level_failure_without_exception_uses_crawl4ai_category(
        self,
    ) -> None:
        """crawl4ai itself reported success=False (no Python exception raised) —
        error_type falls back to the crawl4ai-side failure category since
        there is no exception class to report."""
        result = CrawlResult(
            url="https://example.com",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="Blocked by anti-bot protection: Cloudflare JS challenge",
        )
        outcome = _build_outcome_from_result("https://example.com", result)

        assert outcome["observed"] is True
        assert outcome["error_type"] == "crawl4ai:blocked_anti_bot"
        assert outcome["error_message"] == "Blocked by anti-bot protection: Cloudflare JS challenge"


# ---------------------------------------------------------------------------
# Truncation + masking
# ---------------------------------------------------------------------------


class TestTruncateErrorMessage:
    def test_short_message_is_unchanged(self) -> None:
        assert _truncate_error_message("short error") == "short error"

    def test_long_message_is_truncated_at_documented_limit(self) -> None:
        long_message = "x" * (_ERROR_MESSAGE_MAX_LEN * 3)
        truncated = _truncate_error_message(long_message)

        assert len(truncated) <= _ERROR_MESSAGE_MAX_LEN
        assert truncated != long_message
        assert truncated.startswith("x")

    def test_truncation_limit_is_bounded_and_documented(self) -> None:
        # Pin the actual number so a future accidental change is visible
        # in a diff, not silently absorbed by a symbolic-only assertion.
        assert 200 <= _ERROR_MESSAGE_MAX_LEN <= 500

    def test_masks_token_query_param_before_truncating(self) -> None:
        text = "fetch failed for https://example.com/x?access_token=SUPERSECRET123&y=1"
        masked = _mask_sensitive_query_params(text)

        assert "SUPERSECRET123" not in masked
        assert "access_token=***" in masked

    def test_outcome_for_failed_url_masks_token_in_url_within_error_body(self) -> None:
        error = _http_status_error(
            status=500,
            body={
                "error": "upstream fetch failed for https://example.com/x?api_key=SUPERSECRET123"
            },
        )
        outcome = _outcome_for_failed_url("https://example.com/a", error)

        assert "SUPERSECRET123" not in outcome["error_message"]


# ---------------------------------------------------------------------------
# _extract_correlation_id / _error_type_name — pure helper unit tests
# ---------------------------------------------------------------------------


class TestExtractCorrelationId:
    def test_extracts_from_json_body_shape(self) -> None:
        text = '{"error": "Internal server error", "correlation_id": "188834187d7d"}'
        assert _extract_correlation_id(text) == "188834187d7d"

    def test_returns_none_when_absent(self) -> None:
        assert _extract_correlation_id("plain timeout error, no structured body") is None


class TestErrorTypeName:
    def test_httpx_exception_type_name(self) -> None:
        assert _error_type_name(httpx.ReadTimeout("x")) == "httpx.ReadTimeout"

    def test_builtin_exception_type_name_has_no_module_prefix(self) -> None:
        assert _error_type_name(RuntimeError("x")) == "RuntimeError"


# ---------------------------------------------------------------------------
# Shape sanity — every outcome dict carries the new additive keys
# ---------------------------------------------------------------------------


def test_all_outcome_builders_include_evidence_keys() -> None:
    outcomes = [
        _outcome_for_failed_url("https://example.com/a", httpx.ReadTimeout("x")),
        _outcome_for_not_attempted_url("https://example.com/b"),
    ]
    for outcome in outcomes:
        assert {"error_type", "error_message", "correlation_id", "observed"} <= set(outcome.keys())
