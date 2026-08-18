"""Damage 3 (stop-the-bleeding fix): the template-cluster detector must
report what it observed, not a cause it cannot see.

Near-duplicate content (5+ sibling pages within Hamming distance 3) is a
real, correctly-detected structural fact. But near-duplicate content does
NOT prove an authentication wall — an SPA fallback, a render error, or a
tenant-wide error page produce the exact same signal. On 2026-08-18 this
mislabeling sent a live investigation down the wrong path for an hour: the
customer saw "login wall" and went hunting for credentials that had nothing
to do with the actual problem (13 identical OpenAPI-parser error pages).

This file does NOT change the REJECT behaviour (a rejected page is still
rejected — see test_crawler_anonymous_auth_wall.py and
test_ingest_login_wall_integration.py, both of which must stay green). It
only asserts that:

1. ``detect_anonymous_auth_wall`` no longer reports an auth-specific 0.9
   confidence for a cluster-only observation.
2. The log events crawler.py emits when a page is rejected/degraded/
   audit-only-flagged by the CLUSTER detector no longer name an
   "inlogmuur" (login wall) as the event identity.

``AUTH_WALL_DETECTED_REASON`` / ``DIRTY_CONTENT_REASON`` (the
``connector.sync_runs`` / crawl_jobs error_summary top-level ``reason``
values) are deliberately OUT of scope here — the frontend UI badge switches
on those exact strings (see klai-portal/frontend
``-connector-feedback.tsx``), so changing them is a cross-service, UI-
breaking change outside this damage-limiting fix.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import AnonymousAuthWallDetected, _ingest_crawl_result
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.utils.auth_wall_detector import (
    AuthWallSignal,
    detect_anonymous_auth_wall,
)
from knowledge_ingest.utils.content_fingerprint import compute_simhash

FIXTURES = Path(__file__).parent / "fixtures"
WALLS = FIXTURES / "auth_walls"


class _FakeConn:
    """Minimal asyncpg.Connection stub — sibling-cluster fetch only."""

    def __init__(self, simhashes: list[int]) -> None:
        self._simhashes = simhashes

    async def fetch(self, _query: str, *_args):
        return [{"content_simhash": h} for h in self._simhashes]


def _clean_result(url: str = "https://example.com/error-page") -> CrawlResult:
    """A page that will NOT trip ``classify_auth_wall`` (no cookie header,
    no redirect, word_count high enough) so control flow reaches the
    cluster-only detector.
    """
    text = "Ordinary error page content. " * 20
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html=f"<html><body>{text}</body></html>",
        word_count=len(text.split()),
        success=True,
        links={"internal": []},
        error_message="",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


# ---------------------------------------------------------------------------
# 1. Detector-level: confidence no longer claims auth-specific certainty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_detection_does_not_report_auth_specific_confidence():
    """A pure cluster observation carries no authentication certainty.

    Cluster membership is a deterministic structural fact (hamming distance,
    cluster size) — not a probabilistic auth judgement. The old hardcoded
    0.9 was inherited from v1's tiered confidence design and never actually
    varied with anything about the observation.
    """
    wall_text = (FIXTURES / "auth_walls" / "redcactus_hubspot.md").read_text(encoding="utf-8")
    target = compute_simhash(wall_text)
    conn = _FakeConn([target] * 5)

    signal = await detect_anonymous_auth_wall(
        wall_text,
        org_id="org-1",
        kb_slug="kb-1",
        url="https://x/wall",
        conn=conn,
    )

    assert signal is not None
    assert signal.confidence != 0.9, (
        "cluster-only detection must not report the old auth-specific 0.9 confidence"
    )


# ---------------------------------------------------------------------------
# 2. Crawler-level: log events must not name the presumed cause
# ---------------------------------------------------------------------------


def _cluster_signal() -> AuthWallSignal:
    return AuthWallSignal(
        pattern="template_cluster",
        evidence=("cluster_size=5 hamming<=3",),
        confidence=0.9,
    )


def _logged_event_names(mock_logger: MagicMock) -> list[str]:
    """Extract the event-name (first positional arg) of every log call.

    NOTE: we assert against a mocked ``crawler.logger`` rather than via
    pytest's ``caplog`` fixture. Structlog in this codebase only routes
    through stdlib ``logging`` (which ``caplog`` intercepts) after
    ``knowledge_ingest.app`` has been imported at least once in the test
    session (``setup_logging()`` runs at that module's import time — see
    ``knowledge_ingest/app.py``). Whether that has already happened depends
    on pytest's collection order across the whole suite, so a ``caplog``-based
    assertion here would pass or fail depending on which OTHER test files
    ran first — exactly the kind of order-dependent flake this suite avoids
    elsewhere. Mocking ``crawler.logger`` directly is order-independent.
    """
    names: list[str] = []
    for method in ("info", "warning", "debug", "error"):
        for call in getattr(mock_logger, method).call_args_list:
            if call.args:
                names.append(str(call.args[0]))
    return names


def _patch_ingest_chain(login_wall_mode: str, mock_logger: MagicMock):
    from tests.conftest import make_pg_store_mock

    pg = make_pg_store_mock()
    ingest = AsyncMock(return_value={"chunks": 1})
    return (
        pg,
        ingest,
        [
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler.logger", mock_logger),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch(
                "knowledge_ingest.adapters.crawler.classify_auth_wall",
                return_value=SimpleNamespace(is_walled=False, match_reasons=()),
            ),
            patch(
                "knowledge_ingest.adapters.crawler.detect_anonymous_auth_wall",
                new=AsyncMock(return_value=_cluster_signal()),
            ),
            patch(
                "knowledge_ingest.adapters.crawler.settings.ingest_login_wall_detect_enabled", True
            ),
            patch(
                "knowledge_ingest.adapters.crawler.settings.ingest_login_wall_detect_mode",
                login_wall_mode,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_reject_mode_log_event_does_not_name_login_wall():
    """REJECT mode: exception still raised (behaviour unchanged); the log
    event identity must no longer assert "login wall" as the cause."""
    result = _clean_result()
    pool = MagicMock()
    mock_logger = MagicMock()
    _pg, ingest, patches = _patch_ingest_chain("reject", mock_logger)

    for p in patches:
        p.start()
    try:
        with pytest.raises(AnonymousAuthWallDetected):
            await _ingest_crawl_result(
                pool,
                result,
                result.url,
                org_id="org-1",
                kb_slug="kb-1",
                stored=None,
            )
    finally:
        for p in patches:
            p.stop()

    ingest.assert_not_called()  # behaviour: still rejected, not persisted
    events = _logged_event_names(mock_logger)
    assert events, "expected at least one log call for the reject path"
    assert not any("login_wall" in e for e in events), (
        f"log event still names a login wall as the cause: {events}"
    )


@pytest.mark.asyncio
async def test_degrade_mode_log_event_does_not_name_login_wall():
    """DEGRADE mode: page still ingests with quality_score=0.0 (unchanged);
    the log event identity must no longer assert "login wall"."""
    result = _clean_result()
    pool = MagicMock()
    mock_logger = MagicMock()
    _pg, ingest, patches = _patch_ingest_chain("degrade", mock_logger)

    for p in patches:
        p.start()
    try:
        await _ingest_crawl_result(
            pool,
            result,
            result.url,
            org_id="org-1",
            kb_slug="kb-1",
            stored=None,
        )
    finally:
        for p in patches:
            p.stop()

    ingest.assert_called_once()
    sent_request = ingest.call_args.args[1]
    assert sent_request.extra.get("quality_score") == 0.0  # behaviour unchanged

    events = _logged_event_names(mock_logger)
    assert events, "expected at least one log call for the degrade path"
    assert not any("login_wall" in e for e in events), (
        f"log event still names a login wall as the cause: {events}"
    )


@pytest.mark.asyncio
async def test_audit_only_mode_log_event_does_not_name_login_wall():
    """AUDIT_ONLY mode: page still ingests unchanged (behaviour unchanged);
    the WARNING log event identity must no longer assert "login wall"."""
    result = _clean_result()
    pool = MagicMock()
    mock_logger = MagicMock()
    _pg, ingest, patches = _patch_ingest_chain("audit_only", mock_logger)

    for p in patches:
        p.start()
    try:
        await _ingest_crawl_result(
            pool,
            result,
            result.url,
            org_id="org-1",
            kb_slug="kb-1",
            stored=None,
        )
    finally:
        for p in patches:
            p.stop()

    ingest.assert_called_once()  # behaviour unchanged: audit_only never blocks
    events = _logged_event_names(mock_logger)
    assert events, "expected at least one log call for the audit_only path"
    assert not any("login_wall" in e for e in events), (
        f"log event still names a login wall as the cause: {events}"
    )
