"""An automated source never replaces a stored document with a much smaller one on first sight.

On production history (2026-11-12) 102 of 9,465 replacements kept under half
of the stored version's words. A crawl or connector sync that delivers such a
version is held: the stored version stays active, and the candidate replaces
it only when the same version arrives again at least 12 hours later. An
upload or other user-driven write is that user's decision and is never held.
"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from structlog.testing import capture_logs

from knowledge_ingest.adapters.crawler import _ingest_crawl_result
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.models import IngestRequest
from knowledge_ingest.routes.ingest import ingest_document
from tests.test_enrichment_dedup import (
    _base_patches,
    _make_mock_app,
    _make_mock_conn,
    _patch_procrastinate_exceptions,  # noqa: F401 - autouse fixture
)

_CONNECTOR = "00000000-0000-0000-0000-00000000c0de"
_STORED = "# Opening hours\n\n" + " ".join(f"detail{w:03d}" for w in range(100))
# Keeps 40 of the 100 stored words: retained 0.4, under the 0.5 line.
_SHRUNK = "# Opening hours\n\n" + " ".join(f"detail{w:03d}" for w in range(40))
_TWELVE_HOURS = 12 * 3600


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _active(held: dict | None = None) -> dict:
    extra: dict = {"document_text": _STORED}
    if held is not None:
        extra["held_candidate"] = held
    return {
        "id": "stored-artifact",
        "content_hash": _hash(_STORED),
        "extra": extra,
        "belief_time_start": 0,
    }


def _connector_request(content: str = _SHRUNK) -> IngestRequest:
    return IngestRequest(
        org_id="org-1",
        kb_slug="support",
        path="https://help.example.com/opening-hours",
        content=content,
        source_type="crawl",
        content_type="kb_article",
        source_connector_id=_CONNECTOR,
    )


async def _ingest(req: IngestRequest, active: dict) -> tuple[dict, AsyncMock, AsyncMock, MagicMock]:
    mock_app, bulk_task, _ = _make_mock_app()
    conn = _make_mock_conn()
    create = AsyncMock(return_value="new-artifact")
    extra_patch = AsyncMock()
    async with _base_patches(mock_app):
        with (
            patch(
                "knowledge_ingest.routes.ingest.pg_store.get_active_artifact_state",
                new=AsyncMock(return_value=active),
            ),
            patch("knowledge_ingest.routes.ingest.pg_store.create_artifact", new=create),
            patch("knowledge_ingest.routes.ingest.pg_store.update_artifact_extra", new=extra_patch),
        ):
            result = await ingest_document(conn, req)
    return result, create, extra_patch, bulk_task


@pytest.mark.asyncio
async def test_shrunken_version_from_a_crawl_is_held_on_first_sight() -> None:
    before = int(time.time())

    with (
        capture_logs() as logs,
        patch("knowledge_ingest.derived_state.set_product_state", AsyncMock()) as states,
        patch("knowledge_ingest.routes.ingest.set_product_state", states),
    ):
        result, create, extra_patch, bulk_task = await _ingest(_connector_request(), _active())

    # The stored version keeps its product states exactly as they were.
    states.assert_not_awaited()
    assert result["status"] == "held"
    assert result["reason"] == "shrunken_version_held"
    create.assert_not_awaited()
    bulk_task.configure.assert_not_called()
    artifact_id, patch_body = extra_patch.call_args.args[1:]
    assert artifact_id == "stored-artifact"
    assert patch_body["held_candidate"]["content_hash"] == _hash(_SHRUNK)
    assert patch_body["held_candidate"]["first_seen"] >= before
    assert [e["event"] for e in logs if e["event"].startswith("ingest_shrunken")] == [
        "ingest_shrunken_version_held"
    ]


@pytest.mark.asyncio
async def test_held_version_seen_again_after_twelve_hours_replaces_the_stored_one() -> None:
    held = {"content_hash": _hash(_SHRUNK), "first_seen": int(time.time()) - _TWELVE_HOURS}

    with (
        capture_logs() as logs,
        patch("knowledge_ingest.derived_state.set_product_state", AsyncMock()) as states,
        patch("knowledge_ingest.routes.ingest.set_product_state", states),
    ):
        result, create, _, bulk_task = await _ingest(_connector_request(), _active(held))

    assert result["status"] == "ok"
    create.assert_awaited_once()
    # The accepted version is a new version: its own states, its own queued work.
    assert [c.args[1:] for c in states.await_args_list] == [
        ("new-artifact", "enrichment", "pending"),
        ("new-artifact", "graph", "not_applicable", "graph_disabled"),
    ]
    bulk_task.configure.return_value.defer_async.assert_awaited_once_with(
        artifact_id="new-artifact"
    )
    assert "ingest_shrunken_version_accepted" in [e["event"] for e in logs]


@pytest.mark.asyncio
async def test_held_version_seen_again_within_twelve_hours_stays_held() -> None:
    first_seen = int(time.time()) - _TWELVE_HOURS + 3600
    held = {"content_hash": _hash(_SHRUNK), "first_seen": first_seen}

    result, create, extra_patch, _ = await _ingest(_connector_request(), _active(held))

    assert result["status"] == "held"
    create.assert_not_awaited()
    extra_patch.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_candidate_from_days_ago_is_a_first_sighting_again() -> None:
    # A degraded render that comes back now and then, with good versions in
    # between, must not count its earlier appearance as the first of two.
    held = {"content_hash": _hash(_SHRUNK), "first_seen": int(time.time()) - 6 * _TWELVE_HOURS}

    result, create, extra_patch, _ = await _ingest(_connector_request(), _active(held))

    assert result["status"] == "held"
    create.assert_not_awaited()
    assert extra_patch.call_args.args[2]["held_candidate"]["first_seen"] >= int(time.time()) - 5


@pytest.mark.asyncio
async def test_a_different_shrunken_candidate_restarts_the_hold() -> None:
    held = {
        "content_hash": "an-earlier-candidate",
        "first_seen": int(time.time()) - 2 * _TWELVE_HOURS,
    }
    other = _SHRUNK + " detail099"

    result, create, extra_patch, _ = await _ingest(_connector_request(other), _active(held))

    assert result["status"] == "held"
    create.assert_not_awaited()
    assert extra_patch.call_args.args[2]["held_candidate"]["content_hash"] == _hash(other)


@pytest.mark.asyncio
async def test_the_same_shrink_through_an_upload_replaces_at_once() -> None:
    upload = IngestRequest(
        org_id="org-1",
        kb_slug="support",
        path="uploads/opening-hours.md",
        content=_SHRUNK,
        source_type="file",
        content_type="kb_article",
    )

    result, create, _, _ = await _ingest(upload, _active())

    assert result["status"] == "ok"
    create.assert_awaited_once()


def _crawl_page(text: str) -> CrawlResult:
    return CrawlResult(
        url="https://help.example.com/opening-hours",
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


@pytest.mark.asyncio
async def test_held_crawl_page_is_seen_as_changed_by_the_next_crawl() -> None:
    page = _crawl_page(_SHRUNK)
    stored_hashes = (_hash("<html>stored</html>"), _hash(_STORED))
    ingest = AsyncMock(return_value={"status": "held", "reason": "shrunken_version_held"})
    pg = MagicMock()
    pg.has_active_connector_artifact_for_url = AsyncMock(return_value=True)
    pg.get_crawled_page_markdown = AsyncMock(return_value=None)
    pg.upsert_crawled_page = AsyncMock()
    pg.update_crawled_page_simhash = AsyncMock()

    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", pg),
        patch("knowledge_ingest.crawl_change.pg_store", pg),
        patch("knowledge_ingest.routes.ingest.ingest_document", new=ingest),
        patch(
            "knowledge_ingest.adapters.crawler.detect_anonymous_auth_wall",
            new=AsyncMock(return_value=None),
        ),
    ):
        for _crawl in range(2):
            changed = await _ingest_crawl_result(
                _make_mock_conn(),
                page,
                page.url,
                "org-1",
                "support",
                stored=stored_hashes,
                connector_id=_CONNECTOR,
            )
            assert changed is False

    assert ingest.await_count == 2
    pg.upsert_crawled_page.assert_not_awaited()
    pg.update_crawled_page_simhash.assert_not_awaited()
