"""Damage 2 (stop-the-bleeding fix): error pages must not be persisted as knowledge.

Production incident (2026-08-18): openapi.eu-production.holodeck.voys.nl
served 13 pages that all successfully fetched (HTTP 200) and were all
exactly 92 characters of the same OpenAPI-parser error text:

    #### Failed to parse OpenAPI file
    Please make sure your OpenAPI file is valid and try again

There was no minimum-content-length check anywhere on the crawl-ingest
path, so 5 of those 13 pages were persisted into the customer's knowledge
base with plausible-looking titles (``call``, ``callrecording``, ...) and
became eligible for retrieval in chat.

2026-08-18 (correction after production measurement, n=1426, from
knowledge.crawled_pages): a flat length threshold does NOT work. Two
legitimate short pages measured in production sit inside the same length
band as the error pages:

  - getklai.com/contact (85 chars): "# Contact\nHave a question about
    Klai? Send us a message"
  - help.voys.nl/help-pages-nl (108 chars): "# Help Pages NL\nFreedom: Je
    eerste keer\nDe Voys App..."

A flat threshold above 118 would drop both. A flat threshold below 85
would let every measured error page through (88/92/98/101/118). Length
alone cannot separate these two classes.

What DOES separate them, per the same measurement: error pages exist in
IDENTICAL clusters (5x exactly 92 chars, 3x exactly 98, 3x exactly 101,
3x exactly 88). The two legitimate short pages are each unique — the only
occurrence of their content in the whole dataset.

The rule is therefore two-tier:

1. Hard floor (``_HARD_MIN_CONTENT_LENGTH`` = 30 chars): below this,
   reject unconditionally. Justified by the data: the only pages below 30
   chars in the whole measured set are 3 completely empty (1-char) pages.
   Every real page -- including the shortest legitimate one at 85 chars --
   sits far above 30, so 30 cannot falsely catch real content, while it
   catches the unambiguous empty-page case without needing a cluster
   lookup at all.

2. Soft zone (30 <= length < ``settings.ingest_min_content_length``, 150):
   reject ONLY when the page is a near-duplicate (SimHash Hamming <= 3) of
   >= 2 OTHER pages in the same (org_id, kb_slug) -- reusing the exact
   cluster-lookup mechanism from ``utils/auth_wall_detector.py``
   (``detect_anonymous_auth_wall``), with a lower ``cluster_min`` than its
   default of 5. The lower bound is required by the data: the smallest
   observed error cluster is 3 total pages (2 OTHERS from any one page's
   perspective) -- the default cluster_min=5 would silently let the
   3-page 404 cluster and both 3-page Ascend clusters (98/101 chars)
   through. A unique short page (0 siblings) is always < 2 and is kept.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.config import settings
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.utils.content_fingerprint import compute_simhash

# Reconstructed from the incident report (exact production string not
# available from this environment — no DB/log access). ~91 chars, matching
# the reported "exactly 92 characters" within reconstruction tolerance.
_OPENAPI_PARSE_ERROR_TEXT = (
    "#### Failed to parse OpenAPI file\nPlease make sure your OpenAPI file is valid and try again"
)

# Reconstructed from the production measurement report (2026-08-18,
# knowledge.crawled_pages, n=1426) — exact production strings not available
# from this environment (no DB access). Length is an approximate match to
# the reported ~88-char class ("404 / Page not found"); what matters for
# the test is that it falls inside the 30-150 soft zone, which it does.
_NOT_FOUND_TEXT = "# 404\nPage not found. The page you are looking for does not exist on this site."

# Reconstructed, approximate match to the reported ~85-char length — a
# real, unique page (getklai.com/contact) from the same measurement, in
# the SAME length band as the error pages above. This is the case a flat
# length threshold cannot handle.
_CONTACT_PAGE_TEXT = "# Contact\nHave a question about Klai? Send us a message and we will reply."

# Reconstructed, approximate match to the reported ~108-char length — a
# real, unique page (help.voys.nl/help-pages-nl) from the same
# measurement, also inside the error-page length band.
_HELP_INDEX_TEXT = (
    "# Help Pages NL\nFreedom: Je eerste keer\nDe Voys App: instellen en gebruiken"
    "\nMeer hulp nodig? Contact ons."
)


def _make_mock_conn(sibling_simhashes: list[int] | None = None):
    """Build a mock asyncpg.Connection.

    ``sibling_simhashes`` seeds ``conn.fetch`` with rows shaped like the
    cluster-lookup query in ``utils/auth_wall_detector.py`` expects
    (``{"content_simhash": ...}``) so the reused cluster-lookup mechanism
    sees them as OTHER pages in the same (org_id, kb_slug).
    """
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    rows = [{"content_simhash": h} for h in (sibling_simhashes or [])]
    conn.fetch = AsyncMock(return_value=rows)
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _make_crawl_result(url: str, text: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html=f"<html><body><p>{text}</p></body></html>",
        word_count=len(text.split()),
        success=True,
        links={"internal": []},
        error_message="",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


async def _run_ingest(result: CrawlResult, mock_conn) -> tuple[MagicMock, AsyncMock]:
    """Run ``_ingest_crawl_result`` and return (pg_store mock, ingest mock)."""
    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        mock_ingest.return_value = {"chunks": 1}
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            url=result.url,
            org_id="org-1",
            kb_slug="docs",
            stored=None,
        )
    return mock_pg, mock_ingest


@pytest.mark.asyncio
async def test_empty_page_is_rejected_below_hard_floor():
    """1-char page (production measurement: 3 completely empty pages) is
    always rejected -- no cluster lookup needed, no siblings required."""
    mock_conn = _make_mock_conn()
    result = _make_crawl_result("https://wiki.redcactus.cloud/nl/empty-page", "x")

    mock_pg, mock_ingest = await _run_ingest(result, mock_conn)

    mock_ingest.assert_not_called()
    mock_pg.upsert_crawled_page.assert_not_called()
    # Hard-floor rejection must not even query the cluster lookup.
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_openapi_error_page_with_siblings_is_rejected():
    """92-char OpenAPI error text WITH 4 identical siblings (production:
    5 total pages) must be rejected -- the exact regression case."""
    assert len(_OPENAPI_PARSE_ERROR_TEXT) < settings.ingest_min_content_length
    siblings = [compute_simhash(_OPENAPI_PARSE_ERROR_TEXT)] * 4
    mock_conn = _make_mock_conn(siblings)
    result = _make_crawl_result(
        "https://openapi.eu-production.holodeck.voys.nl/call",
        _OPENAPI_PARSE_ERROR_TEXT,
    )

    mock_pg, mock_ingest = await _run_ingest(result, mock_conn)

    mock_ingest.assert_not_called()
    mock_pg.upsert_crawled_page.assert_not_called()


@pytest.mark.asyncio
async def test_404_page_with_two_siblings_is_rejected():
    """~88-char (reconstructed) '404 Page not found' WITH 2 identical siblings (production:
    3 total pages) must be rejected. This is the case that requires
    cluster_min lower than the auth-wall default of 5 -- a 3-page cluster
    only has 2 OTHERS from any single page's perspective."""
    assert len(_NOT_FOUND_TEXT) < settings.ingest_min_content_length
    siblings = [compute_simhash(_NOT_FOUND_TEXT)] * 2
    mock_conn = _make_mock_conn(siblings)
    result = _make_crawl_result("https://getklai.com/en/docs/legal/dpa", _NOT_FOUND_TEXT)

    mock_pg, mock_ingest = await _run_ingest(result, mock_conn)

    mock_ingest.assert_not_called()
    mock_pg.upsert_crawled_page.assert_not_called()


@pytest.mark.asyncio
async def test_unique_short_contact_page_is_persisted():
    """~85-char (reconstructed) contact page WITHOUT siblings must be persisted.

    This is the most important test of the two-tier rule: it locks in
    that real content is not thrown away. The flat 150-char threshold this
    replaces would have dropped this page -- it sits at 85 chars, squarely
    inside the 88-118 char band the measured error pages also occupy.
    Length alone cannot tell these apart; uniqueness can.
    """
    assert len(_CONTACT_PAGE_TEXT) < settings.ingest_min_content_length
    mock_conn = _make_mock_conn()  # no siblings
    result = _make_crawl_result("https://getklai.com/contact", _CONTACT_PAGE_TEXT)

    _mock_pg, mock_ingest = await _run_ingest(result, mock_conn)

    mock_ingest.assert_called_once()


@pytest.mark.asyncio
async def test_unique_short_help_index_page_is_persisted():
    """~108-char (reconstructed) Voys help index page WITHOUT siblings must be persisted.

    Second most important test of the two-tier rule -- same rationale as
    the contact-page test above, at a different point in the error-page
    length band (88-118 chars).
    """
    assert len(_HELP_INDEX_TEXT) < settings.ingest_min_content_length
    mock_conn = _make_mock_conn()  # no siblings
    result = _make_crawl_result("https://help.voys.nl/help-pages-nl", _HELP_INDEX_TEXT)

    _mock_pg, mock_ingest = await _run_ingest(result, mock_conn)

    mock_ingest.assert_called_once()


@pytest.mark.asyncio
async def test_short_legitimate_page_just_above_threshold_is_persisted():
    """A page at/above the 150-char threshold is always persisted,
    regardless of clustering -- the two-tier rule only applies below it."""
    text = "x" * (settings.ingest_min_content_length + 1)
    mock_conn = _make_mock_conn()
    result = _make_crawl_result("https://example.com/short-but-real", text)

    _mock_pg, mock_ingest = await _run_ingest(result, mock_conn)

    mock_ingest.assert_called_once()


@pytest.mark.asyncio
async def test_hard_floor_rejection_is_logged_with_reason_code():
    """The empty-page skip must be observable, not silent.

    NOTE: asserts against a mocked ``crawler.logger`` rather than
    ``caplog`` — structlog in this codebase only routes through stdlib
    ``logging`` (which ``caplog`` intercepts) after ``knowledge_ingest.app``
    has been imported at least once in the test session. Whether that has
    happened depends on pytest's collection order across the whole suite,
    so a ``caplog``-based assertion here would be order-dependent. Mocking
    ``crawler.logger`` directly is order-independent.
    """
    mock_conn = _make_mock_conn()
    result = _make_crawl_result("https://example.com/empty", "x")

    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    mock_logger = MagicMock()
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch("knowledge_ingest.adapters.crawler.logger", mock_logger),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ),
    ):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            url=result.url,
            org_id="org-1",
            kb_slug="docs",
            stored=None,
        )

    logged_events = [call.args[0] for call in mock_logger.info.call_args_list if call.args]
    assert "crawl_skipped_content_too_short" in logged_events


@pytest.mark.asyncio
async def test_duplicate_cluster_rejection_is_logged_with_reason_code():
    """The short-and-duplicated skip must also be observable."""
    siblings = [compute_simhash(_OPENAPI_PARSE_ERROR_TEXT)] * 4
    mock_conn = _make_mock_conn(siblings)
    result = _make_crawl_result("https://example.com/junk", _OPENAPI_PARSE_ERROR_TEXT)

    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    mock_logger = MagicMock()
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch("knowledge_ingest.adapters.crawler.logger", mock_logger),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ),
    ):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            url=result.url,
            org_id="org-1",
            kb_slug="docs",
            stored=None,
        )

    logged_events = [call.args[0] for call in mock_logger.info.call_args_list if call.args]
    assert "crawl_skipped_content_too_short" in logged_events
