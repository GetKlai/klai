"""SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase C -- crawler ingest integration.

Verifies the v2-specific control flow inside ``_ingest_crawl_result``:

- SimHash is computed once per ingested page (not recomputed inside the
  detector when the crawler already passed it via ``target_simhash``).
- ``pg_store.update_crawled_page_simhash`` is called with the page's hash
  AFTER ``upsert_crawled_page`` so the row exists.
- When detection raises (reject mode), neither ``upsert_crawled_page`` nor
  ``update_crawled_page_simhash`` is called — the page is purely skipped.
- ``conn.fetch`` is invoked with org_id + kb_slug + url args (REQ-09 tenant
  isolation; the SQL filter clauses are pinned in ``test_auth_wall_detector``).

Mode-handling coverage (reject / degrade / audit_only) lives in
``test_ingest_login_wall_integration.py``; this file tests the SimHash
storage hook plus the detector-call contract from the crawler's side.
"""

from __future__ import annotations

import contextlib
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


def _patch_chain(*, cluster_simhashes: list[int] | None = None):
    pool = MagicMock()
    rows = [{"content_simhash": h} for h in (cluster_simhashes or [])]
    pool.fetch = AsyncMock(return_value=rows)

    # ``make_pg_store_mock`` from conftest returns ``AsyncMock(spec=pg_store)``
    # so every helper — current AND any future addition — is auto-mocked
    # as an async no-op. Replaces the previous MagicMock + per-method
    # AsyncMock-assignment pattern that broke whenever a new pg_store
    # helper landed (e.g. ``update_crawled_page_simhash`` from this SPEC).
    from tests.conftest import make_pg_store_mock

    pg_store_mock = make_pg_store_mock()

    ingest_document_mock = AsyncMock(return_value=None)

    link_graph_mock = MagicMock()
    link_graph_mock.get_outbound_urls = AsyncMock(return_value=[])
    link_graph_mock.get_anchor_texts = AsyncMock(return_value={})
    link_graph_mock.get_incoming_count = AsyncMock(return_value=0)

    return pool, pg_store_mock, ingest_document_mock, link_graph_mock


@contextlib.contextmanager
def _common_patches(pg, ingest, lg, *, mode: str = "reject"):
    """Apply the patch chain shared by every test in this file."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("knowledge_ingest.adapters.crawler.pg_store", pg))
        stack.enter_context(
            patch(
                "knowledge_ingest.adapters.crawler._build_image_store",
                return_value=None,
            )
        )
        stack.enter_context(patch("knowledge_ingest.routes.ingest.ingest_document", ingest))
        stack.enter_context(patch("knowledge_ingest.link_graph", lg, create=True))
        stack.enter_context(
            patch(
                "knowledge_ingest.config.settings.ingest_login_wall_detect_enabled",
                True,
            )
        )
        stack.enter_context(
            patch(
                "knowledge_ingest.config.settings.ingest_login_wall_detect_mode",
                mode,
            )
        )
        yield


# ---------------------------------------------------------------------------
# REQ-01: SimHash is stored after ingest
# ---------------------------------------------------------------------------


class TestSimhashStorage:
    @pytest.mark.asyncio()
    async def test_simhash_stored_for_clean_page(self) -> None:
        """After a clean ingest, update_crawled_page_simhash is called with
        the SimHash of the ingested text."""
        clean = _clean_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=[])
        with _common_patches(pg, ingest, lg, mode="reject"):
            await _ingest_crawl_result(
                pool,
                clean,
                clean.url,
                org_id="org-1",
                kb_slug="kb-1",
                stored=None,
                login_indicator_selector=None,
            )

        # SimHash store called exactly once with the right args.
        pg.update_crawled_page_simhash.assert_called_once()
        call = pg.update_crawled_page_simhash.call_args
        # Positional arg 0 is the connection mock; kwargs hold the rest.
        assert call.kwargs["org_id"] == "org-1"
        assert call.kwargs["kb_slug"] == "kb-1"
        assert call.kwargs["url"] == clean.url
        # The stored hash matches what the page text would hash to under v2.
        expected = compute_simhash(clean.fit_markdown)
        assert call.kwargs["content_simhash"] == expected

    @pytest.mark.asyncio()
    async def test_simhash_not_stored_when_rejected(self) -> None:
        """Reject mode raises before ingest → SimHash store not called either.

        The wall page is purely skipped: no Qdrant write, no ``crawled_pages``
        upsert, no ``content_simhash`` update.
        """
        walled = _walled_result()
        target = compute_simhash(walled.fit_markdown)
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=[target] * 5)
        with _common_patches(pg, ingest, lg, mode="reject"):
            with pytest.raises(AnonymousAuthWallDetected):
                await _ingest_crawl_result(
                    pool,
                    walled,
                    walled.url,
                    org_id="org-1",
                    kb_slug="kb-1",
                    stored=None,
                    login_indicator_selector=None,
                )

        pg.upsert_crawled_page.assert_not_called()
        pg.update_crawled_page_simhash.assert_not_called()

    @pytest.mark.asyncio()
    async def test_simhash_stored_in_degrade_mode(self) -> None:
        """Degrade mode ingests + still stores the SimHash for next-crawl
        cluster lookups (the page IS counted as a sibling next time)."""
        walled = _walled_result()
        target = compute_simhash(walled.fit_markdown)
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=[target] * 5)
        with _common_patches(pg, ingest, lg, mode="degrade"):
            await _ingest_crawl_result(
                pool,
                walled,
                walled.url,
                org_id="org-1",
                kb_slug="kb-1",
                stored=None,
                login_indicator_selector=None,
            )

        pg.update_crawled_page_simhash.assert_called_once()
        assert (
            pg.update_crawled_page_simhash.call_args.kwargs["content_simhash"] == target
        )


# ---------------------------------------------------------------------------
# REQ-09: detector receives tenant-scoped DB args
# ---------------------------------------------------------------------------


class TestDetectorWiring:
    @pytest.mark.asyncio()
    async def test_detector_receives_org_kb_url_via_conn_fetch(self) -> None:
        """The detector queries with org_id + kb_slug + url positional args.

        Pins REQ-09 tenant isolation at the call site: even if the detector's
        SQL changes, the crawler must still pass these three values so the
        tenant filter applies.
        """
        clean = _clean_result()
        pool, pg, ingest, lg = _patch_chain(cluster_simhashes=[])
        with _common_patches(pg, ingest, lg, mode="reject"):
            await _ingest_crawl_result(
                pool,
                clean,
                clean.url,
                org_id="org-voys",
                kb_slug="support",
                stored=None,
                login_indicator_selector=None,
            )

        pool.fetch.assert_called_once()
        _query, args = pool.fetch.call_args.args[0], pool.fetch.call_args.args[1:]
        assert "org-voys" in args
        assert "support" in args
        assert clean.url in args


# ---------------------------------------------------------------------------
# Detection disabled → SimHash still computed and stored
# ---------------------------------------------------------------------------


class TestDetectionDisabled:
    @pytest.mark.asyncio()
    async def test_simhash_stored_even_when_detector_disabled(self) -> None:
        """REQ-01: SimHash is computed/stored regardless of detector flag.

        Backfill / validation script need every page's SimHash regardless
        of whether the runtime detector is gated; turning detection off does
        not break operator workflows that rely on the column being populated.
        """
        clean = _clean_result()
        pool, pg, ingest, lg = _patch_chain()
        with (
            patch("knowledge_ingest.adapters.crawler.pg_store", pg),
            patch(
                "knowledge_ingest.adapters.crawler._build_image_store",
                return_value=None,
            ),
            patch("knowledge_ingest.routes.ingest.ingest_document", ingest),
            patch("knowledge_ingest.link_graph", lg, create=True),
            patch(
                "knowledge_ingest.config.settings.ingest_login_wall_detect_enabled",
                False,
            ),
        ):
            await _ingest_crawl_result(
                pool,
                clean,
                clean.url,
                org_id="org-1",
                kb_slug="kb-1",
                stored=None,
                login_indicator_selector=None,
            )

        # Detector NOT called.
        pool.fetch.assert_not_called()
        # SimHash STILL stored.
        pg.update_crawled_page_simhash.assert_called_once()
