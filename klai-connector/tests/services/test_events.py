"""Tests for app.services.events — product event emission helper.

SPEC-CRAWL-003 REQ-15. Covers:
- Fire-and-forget insert with async task scheduling
- Zitadel org_id resolution to portal_orgs.id
- Insert with NULL org_id when zitadel_org_id is None
- Insert with NULL org_id when zitadel_org_id doesn't resolve
- Graceful no-op when session_maker not initialised
- Failure swallowing (sync must never fail because of event emission)
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.events import emit_product_event


async def _drain_pending() -> None:
    """Give the fire-and-forget task a turn to run + complete."""
    for _ in range(5):
        await asyncio.sleep(0)


async def test_emit_product_event_inserts_with_resolved_org_id() -> None:
    """Zitadel org_id → portal_orgs.id lookup succeeds; INSERT uses integer FK."""
    session_mock = AsyncMock()
    # First execute() = SELECT portal_orgs; second = INSERT product_events
    select_result = MagicMock()
    select_result.first = MagicMock(return_value=(42,))
    insert_result = MagicMock()
    session_mock.execute = AsyncMock(side_effect=[select_result, insert_result])
    session_mock.commit = AsyncMock()

    session_maker = MagicMock()
    session_maker.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.events.session_maker", session_maker):
        emit_product_event(
            "knowledge.sync_quality_degraded",
            zitadel_org_id="362757920133283846",
            properties={"connector_id": "abc", "reason": "boilerplate_cluster"},
        )
        await _drain_pending()

    # 2 execute calls: SELECT portal_orgs + INSERT product_events
    assert session_mock.execute.await_count == 2, f"Expected 2 execute() calls, got {session_mock.execute.await_count}"
    insert_call = session_mock.execute.await_args_list[1]
    params = insert_call.args[1]
    assert params["event_type"] == "knowledge.sync_quality_degraded"
    assert params["org_id"] == 42
    props = json.loads(params["properties"])
    assert props["reason"] == "boilerplate_cluster"
    session_mock.commit.assert_awaited_once()


async def test_emit_product_event_null_org_when_not_found() -> None:
    """Unknown Zitadel org_id → INSERT with org_id=NULL (event still recorded)."""
    session_mock = AsyncMock()
    select_result = MagicMock()
    select_result.first = MagicMock(return_value=None)  # no match
    insert_result = MagicMock()
    session_mock.execute = AsyncMock(side_effect=[select_result, insert_result])
    session_mock.commit = AsyncMock()

    session_maker = MagicMock()
    session_maker.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.events.session_maker", session_maker):
        emit_product_event(
            "knowledge.sync_quality_degraded",
            zitadel_org_id="unknown-zitadel-id",
            properties={"x": 1},
        )
        await _drain_pending()

    insert_call = session_mock.execute.await_args_list[1]
    assert insert_call.args[1]["org_id"] is None


async def test_emit_product_event_no_org_skips_lookup() -> None:
    """zitadel_org_id=None → only one execute() call (INSERT, no SELECT)."""
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock()
    session_mock.commit = AsyncMock()

    session_maker = MagicMock()
    session_maker.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.events.session_maker", session_maker):
        emit_product_event(
            "knowledge.sync_quality_degraded",
            zitadel_org_id=None,
            properties={"x": 1},
        )
        await _drain_pending()

    assert session_mock.execute.await_count == 1
    insert_call = session_mock.execute.await_args_list[0]
    assert insert_call.args[1]["org_id"] is None


async def test_emit_product_event_session_maker_uninitialised() -> None:
    """When session_maker is None, emit is a no-op (no crash)."""
    with patch("app.services.events.session_maker", None):
        emit_product_event(
            "knowledge.sync_quality_degraded",
            zitadel_org_id="org-001",
            properties={"x": 1},
        )
        await _drain_pending()
    # No assertions — just verify no exception raised.


async def test_emit_product_event_swallows_db_errors() -> None:
    """DB error during insert is logged and swallowed, never re-raised."""
    session_mock = AsyncMock()
    session_mock.execute = AsyncMock(side_effect=RuntimeError("db unavailable"))
    session_mock.commit = AsyncMock()

    session_maker = MagicMock()
    session_maker.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.events.session_maker", session_maker):
        emit_product_event(
            "knowledge.sync_quality_degraded",
            zitadel_org_id=None,
            properties={"x": 1},
        )
        await _drain_pending()
    # Success if we reach here without exception.
