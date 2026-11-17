"""The sources list carries what the last website crawl read and left for later."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.klai_connector_client import SyncRunData
from tests.conftest import make_perms
from tests.test_app_knowledge_sources_list import (
    _make_connector,
    _make_kb,
    _make_org,
    _make_sources_db,
)

COVERAGE = {
    "pages_read": 80,
    "addresses_gone": 3,
    "addresses_waiting": 120,
    "addresses_known": 203,
    "max_pages": 80,
    "stopped_by": "page_limit",
}


def _run(coverage: dict | None) -> SyncRunData:
    return SyncRunData(
        id="run-1",
        connector_id="conn-web",
        status="completed",
        started_at=datetime(2026, 11, 17, 1, tzinfo=UTC),
        coverage=coverage,
    )


async def _list_sources(get_sync_runs: AsyncMock, web_status: str = "completed") -> dict:
    from app.api.app_knowledge_bases import list_kb_sources

    db = _make_sources_db(
        [
            _make_connector("conn-web", "web_crawler", "Docs site", status=web_status),
            _make_connector("conn-notion", "notion", "Wiki", status="completed"),
        ]
    )
    aggregates = {
        "connectors": [
            {"connector_id": "conn-web", "items_count": 80, "chunks_count": 200},
            {"connector_id": "conn-notion", "items_count": 5, "chunks_count": 20},
        ],
        "uploads": [],
    }
    with (
        patch("app.api.app_knowledge_bases._load_org_or_500", new=AsyncMock(return_value=_make_org())),
        patch("app.api.app_knowledge_bases._get_kb_or_404", new=AsyncMock(return_value=_make_kb())),
        patch(
            "app.api.app_knowledge_bases.knowledge_ingest_client.get_kb_sources",
            new=AsyncMock(return_value=aggregates),
        ),
        patch("app.api.app_knowledge_bases.klai_connector_client.get_sync_runs", new=get_sync_runs),
    ):
        result = await list_kb_sources(
            kb_slug="kb-a", perms=make_perms(role="admin", user_id="user-1", org_id=1), db=db
        )
    return {s.id: s for s in result.sources}


@pytest.mark.asyncio
async def test_website_source_carries_the_coverage_of_its_last_run_and_others_do_not() -> None:
    get_sync_runs = AsyncMock(return_value=[_run(COVERAGE)])

    by_id = await _list_sources(get_sync_runs)

    assert by_id["conn-web"].sync_coverage == COVERAGE
    assert by_id["conn-notion"].sync_coverage is None
    get_sync_runs.assert_awaited_once_with("conn-web", org_id="zitadel-org-1", limit=1, timeout=2.0)


@pytest.mark.asyncio
async def test_unreachable_connector_service_leaves_the_list_intact_without_coverage() -> None:
    get_sync_runs = AsyncMock(side_effect=httpx.ConnectError("down"))

    by_id = await _list_sources(get_sync_runs)

    assert by_id["conn-web"].sync_coverage is None
    assert by_id["conn-web"].items_count == 80


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("not json"), TypeError("unexpected shape")])
async def test_a_malformed_connector_answer_leaves_the_list_intact(error: Exception) -> None:
    by_id = await _list_sources(AsyncMock(side_effect=error))

    assert by_id["conn-web"].sync_coverage is None
    assert by_id["conn-web"].items_count == 80


@pytest.mark.asyncio
async def test_coverage_is_only_asked_for_a_website_whose_last_sync_completed() -> None:
    get_sync_runs = AsyncMock(return_value=[_run(COVERAGE)])

    by_id = await _list_sources(get_sync_runs, web_status="failed")

    assert by_id["conn-web"].sync_coverage is None
    get_sync_runs.assert_not_awaited()
