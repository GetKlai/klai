"""Unit tests for ``pg_store.update_crawled_page_simhash``.

Pin the behaviour added in the SPEC-INGEST-LOGIN-WALL-DETECT-002
follow-ups: the helper uses ``RETURNING url`` and emits a structlog
warning when 0 rows are affected (= the row was deleted between
``upsert_crawled_page`` and the simhash UPDATE — typically a race with
``delete_kb`` / ``delete_connector_artifacts``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from knowledge_ingest import pg_store


@pytest.mark.asyncio
async def test_update_returns_url_on_success() -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="https://x/page-1")

    await pg_store.update_crawled_page_simhash(
        conn,
        org_id="org-1",
        kb_slug="kb-1",
        url="https://x/page-1",
        content_simhash=42,
    )

    conn.fetchval.assert_awaited_once()
    sql = conn.fetchval.await_args.args[0]
    assert "UPDATE knowledge.crawled_pages" in sql
    assert "RETURNING url" in sql, "RETURNING clause missing — race-detection broken"


@pytest.mark.asyncio
async def test_update_warns_when_no_row() -> None:
    """fetchval returns None → structlog warning event, no exception raised.

    Uses ``structlog.testing.capture_logs`` so the test does not depend on
    structlog's stdlib-logging routing (which is configured per-process and
    can be silenced by other tests).
    """
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=None)

    with capture_logs() as logs:
        # Must not raise — fingerprint storage is non-critical.
        await pg_store.update_crawled_page_simhash(
            conn,
            org_id="org-1",
            kb_slug="kb-1",
            url="https://x/missing-row",
            content_simhash=42,
        )

    matched = [
        log
        for log in logs
        if log.get("event") == "crawled_pages_simhash_update_no_row"
        and log.get("log_level") == "warning"
    ]
    assert matched, (
        "expected structlog warning event crawled_pages_simhash_update_no_row"
    )
    assert matched[0]["url"] == "https://x/missing-row"
    assert matched[0]["org_id"] == "org-1"
    assert matched[0]["kb_slug"] == "kb-1"


@pytest.mark.asyncio
async def test_update_passes_tenant_filters() -> None:
    """SQL filters by org_id AND kb_slug AND url — REQ-09 isolation."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value="https://x/page-1")

    await pg_store.update_crawled_page_simhash(
        conn,
        org_id="org-voys",
        kb_slug="support",
        url="https://x/page-1",
        content_simhash=42,
    )

    sql, *args = conn.fetchval.await_args.args
    assert "WHERE org_id = $2" in sql
    assert "AND kb_slug = $3" in sql
    assert "AND url = $4" in sql
    # Args bound positionally: (sql, content_simhash, org_id, kb_slug, url)
    assert args[1] == "org-voys"
    assert args[2] == "support"
    assert args[3] == "https://x/page-1"
