"""Integration tests for SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase C.

REQ-07 (reject / degrade / audit_only modes) + REQ-05 (config flags)
covered here.

We test ``_ingest_crawl_result`` end-to-end with the v2 cluster detector
wired in. Real Postgres / Qdrant / S3 are mocked — we verify *control flow*:
did we raise, did we set ``extra["quality_score"]``, did we log, did we
still call ``ingest_document``?

The detector now requires a DB query (``pool.fetch`` is awaited inside
``detect_anonymous_auth_wall``). Walled tests inject a fake cluster
(5 siblings sharing the wall's SimHash) so the detector fires; clean tests
inject an empty result so the detector returns None.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import (
    AnonymousAuthWallDetected,
    _ingest_crawl_result,
)
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.utils.content_fingerprint import compute_simhash

FIXTURES = Path(__file__).parent / "fixtures"


def _walled_result() -> CrawlResult:
    """Build a CrawlResult that contains the canonical login-wall phrase."""
    raw = (FIXTURES / "auth_walls" / "redcactus_hubspot.md").read_text(encoding="utf-8")
    return CrawlResult(
        url="https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
        fit_markdown=raw,
        raw_markdown=raw,
        html="<html><body>" + raw + "</body></html>",
        word_count=3243,
        success=True,
        error_message=None,
        response_headers={"content-type": "text/html"},
        media={},
        links={},
        metadata={},
    )


def _clean_result() -> CrawlResult:
    raw = (FIXTURES / "clean_pages" / "redcactus_ifttt.md").read_text(encoding="utf-8")
    return CrawlResult(
        url="https://wiki.redcactus.cloud/nl/crm-software/IFTTT",
        fit_markdown=raw,
        raw_markdown=raw,
        html="<html><body>" + raw + "</body></html>",
        word_count=1096,
        success=True,
        error_message=None,
        response_headers={"content-type": "text/html"},
        media={},
        links={},
        metadata={},
    )


def _embedded_gate_result() -> CrawlResult:
    raw = (
        "## Public CRM integration\n"
        + "This public section contains useful implementation detail. " * 80
        + "\n## [Log in](https://wiki.example.test/login?redirect_to=/private) "
        "when you want to read this article\n"
        "This article is available to authenticated users. Sign in to access "
        "the protected content and documentation.\n"
        + "More public text after the protected section. " * 50
    )
    return CrawlResult(
        url="https://wiki.example.test/nl/crm-software/example",
        fit_markdown=raw,
        raw_markdown=raw,
        html="<html><body>" + raw + "</body></html>",
        word_count=900,
        success=True,
        error_message=None,
        response_headers={"content-type": "text/html"},
        media={},
        links={},
        metadata={},
    )


# Common mocks: stub everything except the login-wall branching logic.
def _patch_chain(*, cluster_simhashes: list[int] | None = None):
    """Patch all external dependencies of ``_ingest_crawl_result``.

    Args:
        cluster_simhashes: Optional list of SimHash values returned by
            ``pool.fetch`` to simulate sibling pages. Walled tests inject
            5+ entries matching the wall's SimHash so the detector fires;
            clean tests omit / pass [] so the detector returns None.
    """
    pool = MagicMock()
    rows = [{"content_simhash": h} for h in (cluster_simhashes or [])]
    pool.fetch = AsyncMock(return_value=rows)

    # ``make_pg_store_mock`` from conftest returns
    # ``AsyncMock(spec=pg_store)`` so every helper — current AND any
    # future addition — is auto-mocked as an async no-op. Avoids the
    # fragility of manually re-listing AsyncMock assignments per helper
    # (which silently breaks when a new pg_store helper is added — the
    # case that hit ``update_crawled_page_simhash`` in this SPEC).
    from tests.conftest import make_pg_store_mock

    pg_store_mock = make_pg_store_mock()

    ingest_document_mock = AsyncMock(return_value=None)

    # _build_image_store returns None when garage is unconfigured (test default).
    # link_graph helpers return empty.
    link_graph_mock = MagicMock()
    link_graph_mock.get_outbound_urls = AsyncMock(return_value=[])
    link_graph_mock.get_anchor_texts = AsyncMock(return_value={})
    link_graph_mock.get_incoming_count = AsyncMock(return_value=0)

    return pool, pg_store_mock, ingest_document_mock, link_graph_mock


def _wall_cluster(result: CrawlResult, *, count: int = 5) -> list[int]:
    """Return ``count`` SimHashes identical to the result's content fingerprint.

    Models the production scenario where a templated wall stub is duplicated
    across many URLs in the same KB; injecting these into ``pool.fetch`` makes
    the v2 detector flag the page.
    """
    text = result.fit_markdown or result.raw_markdown
    return [compute_simhash(text)] * count


# ---------------------------------------------------------------------------
# REQ-03 — Reject behaviour
# ---------------------------------------------------------------------------


class TestRejectMode:
    """AC-03.1: default mode raises AnonymousAuthWallDetected on walled page."""

    @pytest.mark.asyncio()
    async def test_walled_page_raises_in_reject_mode(self) -> None:
        walled = _walled_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=_wall_cluster(walled))
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_mode", "reject"),
        ):
            with pytest.raises(AnonymousAuthWallDetected) as excinfo:
                await _ingest_crawl_result(
                    pool,
                    walled,
                    "https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
                    org_id="100000000000000002",
                    kb_slug="support",
                    stored=None,
                    login_indicator_selector=None,
                )

        assert excinfo.value.url == "https://wiki.redcactus.cloud/nl/crm-software/HubSpot"
        assert excinfo.value.signal.pattern == "auth_wall_classifier"
        assert "embedded_login_gate" in excinfo.value.signal.evidence
        # ingest_document MUST NOT have been called for a rejected page.
        ingest.assert_not_called()
        # crawled_pages MUST NOT have been updated (no Postgres write either).
        pg.upsert_crawled_page.assert_not_called()

    @pytest.mark.asyncio()
    async def test_single_embedded_login_gate_raises_before_cluster_detector(self) -> None:
        walled = _embedded_gate_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=[])
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_mode", "reject"),
        ):
            with pytest.raises(AnonymousAuthWallDetected) as excinfo:
                await _ingest_crawl_result(
                    pool,
                    walled,
                    walled.url,
                    org_id="100000000000000002",
                    kb_slug="support",
                    stored=None,
                    login_indicator_selector=None,
                )

        assert excinfo.value.url == walled.url
        assert excinfo.value.signal.pattern == "auth_wall_classifier"
        assert "embedded_login_gate" in excinfo.value.signal.evidence
        ingest.assert_not_called()
        pg.upsert_crawled_page.assert_not_called()
        pool.fetch.assert_not_called()


class TestDegradeMode:
    """AC-03.2: degrade mode ingests with quality_score=0.0 + warning metadata."""

    @pytest.mark.asyncio()
    async def test_walled_page_ingests_with_quality_score_zero(self) -> None:
        walled = _walled_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=_wall_cluster(walled))
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_mode", "degrade"),
        ):
            await _ingest_crawl_result(
                pool,
                walled,
                "https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
                org_id="100000000000000002",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
            )

        ingest.assert_called_once()
        sent_request = ingest.call_args.args[1]
        assert sent_request.extra.get("quality_score") == 0.0
        assert sent_request.extra.get("ingest_warning") == "login_wall_detected"


class TestAuditOnlyMode:
    """AC-03.3: audit_only ingests unchanged + emits warn log."""

    @pytest.mark.asyncio()
    async def test_walled_page_ingests_unchanged(self) -> None:
        walled = _walled_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=_wall_cluster(walled))
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch(
                "knowledge_ingest.config.settings.ingest_login_wall_detect_mode",
                "audit_only",
            ),
        ):
            await _ingest_crawl_result(
                pool,
                walled,
                "https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
                org_id="100000000000000002",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
            )

        ingest.assert_called_once()
        sent_request = ingest.call_args.args[1]
        # No quality_score override in audit_only.
        assert "quality_score" not in sent_request.extra
        assert "ingest_warning" not in sent_request.extra


class TestCleanPageUntouched:
    """Sanity: clean pages are not affected by any mode."""

    @pytest.mark.asyncio()
    @pytest.mark.parametrize("mode", ["reject", "degrade", "audit_only"])
    async def test_clean_page_ingests_normally(self, mode: str) -> None:
        pool, pg, ingest, lg = _patch_chain()
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_mode", mode),
        ):
            await _ingest_crawl_result(
                pool,
                _clean_result(),
                "https://wiki.redcactus.cloud/nl/crm-software/IFTTT",
                org_id="100000000000000002",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
            )

        # Clean page → ingest called, no quality override, no warning.
        ingest.assert_called_once()
        sent_request = ingest.call_args.args[1]
        assert "quality_score" not in sent_request.extra
        assert "ingest_warning" not in sent_request.extra


class TestAuthenticatedClusterHeuristic:
    """Authenticated crawls must not reject pages only because templates match."""

    @pytest.mark.asyncio()
    async def test_authenticated_context_skips_template_cluster_detector(self) -> None:
        clean = _clean_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=_wall_cluster(clean))
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_mode", "reject"),
        ):
            await _ingest_crawl_result(
                pool,
                clean,
                clean.url,
                org_id="100000000000000002",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
                authenticated_context=True,
            )

        pool.fetch.assert_not_called()
        ingest.assert_called_once()
        sent_request = ingest.call_args.args[1]
        assert "quality_score" not in sent_request.extra
        assert "ingest_warning" not in sent_request.extra


# ---------------------------------------------------------------------------
# REQ-05 — Configuration
# ---------------------------------------------------------------------------


class TestDisabledFlag:
    """AC-05.1: ENABLED=False short-circuits the detector entirely."""

    @pytest.mark.asyncio()
    async def test_disabled_skips_detection(self) -> None:
        pool, pg, ingest, lg = _patch_chain()
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", False),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_mode", "reject"),
        ):
            # Even in reject mode, a walled page should ingest because flag is off.
            await _ingest_crawl_result(
                pool,
                _walled_result(),
                "https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
                org_id="100000000000000002",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
            )

        ingest.assert_called_once()
        sent_request = ingest.call_args.args[1]
        assert "quality_score" not in sent_request.extra
        assert "ingest_warning" not in sent_request.extra


class TestInvalidModeFailsSafe:
    """AC-05.2: invalid mode falls back to audit_only without crashing."""

    @pytest.mark.asyncio()
    async def test_invalid_mode_treated_as_audit_only(self) -> None:
        """Invalid mode must NOT crash and MUST NOT raise on a walled page.

        The fail-safe is: if the configured mode is unrecognised, treat as
        audit_only (log + ingest). Never block the entire crawl pipeline due
        to a config typo.
        """
        walled = _walled_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=_wall_cluster(walled))
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch("knowledge_ingest.config.settings.ingest_login_wall_detect_enabled", True),
            patch(
                "knowledge_ingest.config.settings.ingest_login_wall_detect_mode",
                "garbage_value",
            ),
        ):
            # Should NOT raise.
            await _ingest_crawl_result(
                pool,
                walled,
                "https://wiki.redcactus.cloud/nl/crm-software/HubSpot",
                org_id="100000000000000002",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
            )

        ingest.assert_called_once()


# ---------------------------------------------------------------------------
# Regression: existing AuthWallDetected (cookie-based) path unchanged
# ---------------------------------------------------------------------------


class TestAuthenticatedPathPreserved:
    """AC-04.4 (Phase C): existing AuthWallDetected behaviour is unchanged.

    When ``login_indicator_selector`` is set AND result.success=False, we
    still raise the original ``AuthWallDetected`` (NOT
    ``AnonymousAuthWallDetected``). This preserves the BFS-halting behaviour
    that SPEC-CRAWLER-004 relies on.
    """

    @pytest.mark.asyncio()
    async def test_existing_auth_wall_path_unchanged(self) -> None:
        from knowledge_ingest.adapters.crawler import AuthWallDetected

        failed_result = CrawlResult(
            url="https://wiki.example/private",
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="wait_for timeout",
        )
        pool, pg, ingest, lg = _patch_chain()
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch("knowledge_ingest.adapters.crawler._build_image_store", return_value=None),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
        ):
            with pytest.raises(AuthWallDetected) as excinfo:
                await _ingest_crawl_result(
                    pool,
                    failed_result,
                    failed_result.url,
                    org_id="org",
                    kb_slug="support",
                    stored=None,
                    login_indicator_selector="#login-form",
                )

        assert excinfo.value.selector == "#login-form"
