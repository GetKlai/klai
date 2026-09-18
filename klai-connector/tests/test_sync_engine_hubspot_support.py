"""Acceptance tests for the hubspot_support branch in SyncEngine.

SPEC-RAG-SUPPORT-GAP (support-gap-detection.md). The support-case sync is
routed to the portal evidence sink, NEVER to knowledge ingestion, and
reconcile only runs after a fully successful snapshot with every case
write durable.

The reader is injected through ``support_reader_factory`` so these tests
exercise the engine's orchestration/gating without touching HubSpot.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.enums import SyncStatus
from app.services.portal_client import PortalClient, PortalConnectorConfig
from app.services.support_source import (
    HubSpotAccountMismatchError,
    HubSpotPartialFailureError,
    SupportCase,
)
from app.services.sync_engine import SyncEngine


def _portal_config() -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id=str(uuid.uuid4()),
        kb_id=1,
        kb_slug="support",
        zitadel_org_id="100000000000000002",
        connector_type="hubspot_support",
        config={"access_token": "tok", "account_id": "12345", "lookback_days": 30},  # noqa: S106
        schedule=None,
        is_enabled=True,
    )


def _case(external_id: str, *, complete: bool = True) -> SupportCase:
    return SupportCase(
        account_id="12345",
        external_id=external_id,
        subject=f"case {external_id}",
        language=None,
        source_url=None,
        source_updated_at="2026-09-16T09:00:00Z",
        complete=complete,
        incomplete_reasons=[] if complete else ["partial_inbox_scope"],
        messages=[],
        metadata={},
    )


def _sync_run_mock() -> MagicMock:
    sync_run = MagicMock()
    sync_run.status = SyncStatus.RUNNING
    sync_run.completed_at = None
    sync_run.cursor_state = None
    sync_run.quality_status = None
    sync_run.error_details = None
    return sync_run


def _make_engine(
    *,
    reader: MagicMock,
    send_results: list[dict[str, Any]] | None = None,
    send_exc: Exception | None = None,
) -> tuple[SyncEngine, MagicMock, MagicMock]:
    """Return (engine, sync_run, portal_client) wired with a fake reader."""
    sync_run = _sync_run_mock()

    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = AsyncMock(return_value=sync_run)
    session.commit = AsyncMock()
    session_maker = MagicMock(return_value=session)

    portal_client = MagicMock()
    portal_client.report_sync_status = AsyncMock()
    if send_exc is not None:
        portal_client.send_support_case = AsyncMock(side_effect=send_exc)
    else:
        results = send_results or [{"case_id": "c", "status": "analyzed", "changed": True, "findings_count": 0}]
        portal_client.send_support_case = AsyncMock(side_effect=results)
    portal_client.reconcile_support_cases = AsyncMock()

    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()

    engine = SyncEngine(
        session_maker=session_maker,
        registry=MagicMock(),
        ingest_client=ingest_client,
        portal_client=portal_client,
        settings=MagicMock(),
        support_reader_factory=lambda _config: reader,
    )
    return engine, sync_run, portal_client


def _reader(
    *,
    tickets: list[dict[str, Any]],
    cases: dict[str, Any] | None = None,
    fetch_exc: Exception | None = None,
    verify_exc: Exception | None = None,
) -> MagicMock:
    reader = MagicMock()
    reader.verify_account = AsyncMock(side_effect=verify_exc)
    reader.select_tickets = AsyncMock(return_value=tickets)
    if fetch_exc is not None:
        reader.fetch_case = AsyncMock(side_effect=fetch_exc)
    else:
        mapping = cases or {}

        async def _fetch(ticket: dict[str, Any]) -> Any:
            return mapping[ticket["id"]]

        reader.fetch_case = AsyncMock(side_effect=_fetch)
    reader.aclose = AsyncMock()
    return reader


# ---------------------------------------------------------------------------


async def test_support_sync_never_calls_knowledge_ingest() -> None:
    reader = _reader(tickets=[{"id": "201"}], cases={"201": _case("201")})
    engine, _sync_run, portal = _make_engine(reader=reader)

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    engine._ingest_client.ingest_document.assert_not_awaited()
    portal.send_support_case.assert_awaited_once()


async def test_full_success_reconciles_with_external_ids_and_completes() -> None:
    reader = _reader(
        tickets=[{"id": "201"}, {"id": "202"}],
        cases={"201": _case("201"), "202": _case("202")},
    )
    engine, sync_run, portal = _make_engine(
        reader=reader,
        send_results=[
            {"case_id": "a", "status": "analyzed", "changed": True, "findings_count": 1},
            {"case_id": "b", "status": "analyzed", "changed": False, "findings_count": 0},
        ],
    )

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    portal.reconcile_support_cases.assert_awaited_once()
    external_ids = portal.reconcile_support_cases.await_args.kwargs["external_ids"]
    assert sorted(external_ids) == ["201", "202"]
    assert sync_run.status == SyncStatus.COMPLETED
    # Only a fully successful snapshot advances the checkpoint.
    assert sync_run.cursor_state is not None
    assert "last_synced_at" in sync_run.cursor_state


async def test_pending_analysis_blocks_reconcile_and_does_not_advance_cursor() -> None:
    reader = _reader(tickets=[{"id": "201"}], cases={"201": _case("201")})
    engine, sync_run, portal = _make_engine(
        reader=reader,
        send_results=[{"case_id": "a", "status": "pending", "changed": True, "findings_count": 0}],
    )

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    # Evidence was stored, but a non-analyzed case is not a success.
    portal.send_support_case.assert_awaited_once()
    portal.reconcile_support_cases.assert_not_awaited()
    assert sync_run.status == SyncStatus.FAILED
    assert sync_run.cursor_state is None, "failed run must not advance the checkpoint"


async def test_incomplete_case_blocks_reconcile_even_when_analyzed() -> None:
    # complete=False (e.g. unresolved truncation / partial inbox scope) is
    # posted as evidence but must not count as a synced case.
    reader = _reader(tickets=[{"id": "201"}], cases={"201": _case("201", complete=False)})
    engine, sync_run, portal = _make_engine(
        reader=reader,
        send_results=[{"case_id": "a", "status": "analyzed", "changed": True, "findings_count": 0}],
    )

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    portal.send_support_case.assert_awaited_once()
    portal.reconcile_support_cases.assert_not_awaited()
    assert sync_run.status == SyncStatus.FAILED
    assert sync_run.cursor_state is None


async def test_out_of_scope_case_is_skipped_not_failed() -> None:
    # One ticket is out of the selected inbox scope (fetch_case -> None); the
    # run still completes and reconciles the in-scope case only.
    reader = _reader(tickets=[{"id": "201"}, {"id": "202"}], cases={"201": _case("201"), "202": None})
    engine, sync_run, portal = _make_engine(reader=reader)

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    # Only the in-scope case is posted; the excluded one is never sent.
    portal.send_support_case.assert_awaited_once()
    portal.reconcile_support_cases.assert_awaited_once()
    external_ids = portal.reconcile_support_cases.await_args.kwargs["external_ids"]
    assert external_ids == ["201"], "excluded case absent -> reconcile removes any stale copy"
    assert sync_run.status == SyncStatus.COMPLETED


async def test_partial_case_failure_marks_failed_and_skips_reconcile() -> None:
    # Two tickets; the second raises a partial-fetch failure.
    reader = MagicMock()
    reader.verify_account = AsyncMock()
    reader.select_tickets = AsyncMock(return_value=[{"id": "201"}, {"id": "202"}])
    reader.aclose = AsyncMock()

    async def _fetch(ticket: dict[str, Any]) -> Any:
        if ticket["id"] == "202":
            raise HubSpotPartialFailureError("thread page 500")
        return _case("201")

    reader.fetch_case = AsyncMock(side_effect=_fetch)

    engine, sync_run, portal = _make_engine(reader=reader)
    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    # The healthy case was still posted (evidence persisted before analysis)...
    portal.send_support_case.assert_awaited_once()
    # ...but a partial snapshot must never reconcile deletions.
    portal.reconcile_support_cases.assert_not_awaited()
    assert sync_run.status == SyncStatus.FAILED


@pytest.mark.parametrize(("status_code", "fetched"), [(403, 1), (503, 2)])
async def test_revoked_access_stops_fetching_while_transient_errors_continue(status_code: int, fetched: int) -> None:
    reader = _reader(
        tickets=[{"id": "201"}, {"id": "202"}],
        cases={"201": _case("201"), "202": _case("202")},
    )
    response = httpx.Response(status_code, request=httpx.Request("POST", "http://portal/support-cases"))
    engine, sync_run, portal = _make_engine(reader=reader)
    portal.send_support_case.side_effect = [
        httpx.HTTPStatusError("import rejected", request=response.request, response=response),
        {"status": "analyzed"},
    ]

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    assert reader.fetch_case.await_count == fetched
    assert reader.fetch_case.await_args_list[0].args == ({"id": "201"},)
    reader.aclose.assert_awaited_once()
    assert portal.send_support_case.await_count == fetched
    portal.reconcile_support_cases.assert_not_awaited()
    assert sync_run.status == SyncStatus.FAILED
    assert sync_run.cursor_state is None
    assert sync_run.documents_failed == 1
    assert portal.report_sync_status.await_args.kwargs["sync_status"] == SyncStatus.FAILED


@pytest.mark.parametrize("http_denial", [True, False])
async def test_config_rejection_reports_terminal_failure_to_portal(http_denial: bool) -> None:
    reader = _reader(tickets=[])
    engine, sync_run, portal = _make_engine(reader=reader)
    connector_id, run_id = uuid.uuid4(), uuid.uuid4()
    sync_run.connector_id = connector_id
    response = httpx.Response(403, request=httpx.Request("GET", "http://portal/internal/connectors/test"))
    error = (
        httpx.HTTPStatusError("feature_not_unlocked", request=response.request, response=response)
        if http_denial
        else httpx.ConnectError("portal unavailable")
    )
    portal.get_connector_config = AsyncMock(side_effect=error)

    await engine._execute_sync(connector_id, run_id)

    assert sync_run.status == SyncStatus.FAILED
    reader.verify_account.assert_not_awaited()
    portal.report_sync_status.assert_awaited_once()
    reported = portal.report_sync_status.await_args.kwargs
    assert reported["connector_id"] == connector_id
    assert reported["sync_run_id"] == run_id
    assert reported["sync_status"] == SyncStatus.FAILED


async def test_post_returning_failed_status_blocks_reconcile() -> None:
    reader = _reader(tickets=[{"id": "201"}], cases={"201": _case("201")})
    engine, sync_run, portal = _make_engine(
        reader=reader,
        send_results=[{"case_id": "a", "status": "failed", "changed": False, "findings_count": 0}],
    )

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    portal.reconcile_support_cases.assert_not_awaited()
    assert sync_run.status == SyncStatus.FAILED


async def test_account_mismatch_fails_run_without_posting_or_reconcile() -> None:
    reader = _reader(
        tickets=[{"id": "201"}],
        cases={"201": _case("201")},
        verify_exc=HubSpotAccountMismatchError("portal 99999 != configured 12345"),
    )
    engine, sync_run, portal = _make_engine(reader=reader)

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    reader.select_tickets.assert_not_awaited()
    portal.send_support_case.assert_not_awaited()
    portal.reconcile_support_cases.assert_not_awaited()
    assert sync_run.status == SyncStatus.FAILED


async def test_every_selected_ticket_is_fetched_regardless_of_mtime() -> None:
    """The engine re-fetches every selected case each run; there is no

    ticket-mtime gate that could skip a case whose notes/messages changed.
    """
    reader = _reader(
        tickets=[{"id": "201"}, {"id": "202"}, {"id": "203"}],
        cases={"201": _case("201"), "202": _case("202"), "203": _case("203")},
    )
    engine, _sync_run, portal = _make_engine(
        reader=reader,
        send_results=[{"case_id": "x", "status": "analyzed", "changed": True, "findings_count": 0}] * 3,
    )

    await engine._run_hubspot_support_sync(
        portal_config=_portal_config(),
        connector_id=uuid.uuid4(),
        sync_run_id=uuid.uuid4(),
        start_time=datetime.now(UTC).timestamp(),
    )

    assert reader.fetch_case.await_count == 3
    assert portal.send_support_case.await_count == 3


# ---------------------------------------------------------------------------
# PortalClient support-case routes (transport-observable exact URL + status)
# ---------------------------------------------------------------------------


def _portal_client() -> PortalClient:
    settings = SimpleNamespace(portal_api_url="http://portal-api:8100", portal_internal_secret="s")  # noqa: S106
    return PortalClient(settings)  # type: ignore[arg-type]


async def test_support_case_routes_use_api_internal_prefix() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(200, json={"case_id": "c", "status": "analyzed", "changed": False, "findings_count": 0})

    portal = _portal_client()
    cid = uuid.uuid4()

    post_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("app.services.portal_client.httpx.AsyncClient", MagicMock(return_value=post_client)):
        result = await portal.send_support_case(cid, {"external_id": "201"})

    reconcile_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("app.services.portal_client.httpx.AsyncClient", MagicMock(return_value=reconcile_client)):
        await portal.reconcile_support_cases(cid, external_ids=["201"])

    assert result["status"] == "analyzed"
    assert seen == [
        ("POST", f"http://portal-api:8100/api/internal/connectors/{cid}/support-cases"),
        ("POST", f"http://portal-api:8100/api/internal/connectors/{cid}/support-cases/reconcile"),
    ]


async def test_send_support_case_raises_on_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    portal = _portal_client()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with (
        patch("app.services.portal_client.httpx.AsyncClient", MagicMock(return_value=client)),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await portal.send_support_case(uuid.uuid4(), {"external_id": "201"})
