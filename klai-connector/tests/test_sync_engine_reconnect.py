"""Integration test for SyncEngine's OAuthReconnectRequiredError handling.

SPEC-KB-MS-DOCS-001 reconnect-signal: when an OAuth adapter raises
``OAuthReconnectRequiredError`` during a sync run, the engine must
report ``sync_status=AUTH_ERROR`` to the portal so the UI can surface
the Reconnect affordance.

This test mocks the heavy dependencies (portal client, session maker,
registry) and exercises only the catch path that translates the typed
exception into ``SyncStatus.AUTH_ERROR``.
"""

# ruff: noqa: S106  -- test-only placeholder token strings

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.oauth_base import OAuthReconnectRequiredError
from app.core.enums import SyncStatus
from app.services.portal_client import PortalConnectorConfig
from app.services.sync_engine import SyncEngine


def _portal_config(connector_type: str = "ms_docs") -> PortalConnectorConfig:
    """Minimal PortalConnectorConfig for a sync run."""
    return PortalConnectorConfig(
        connector_id="conn-uuid-reconnect",
        kb_id=1,
        kb_slug="test-kb",
        zitadel_org_id="org-123",
        connector_type=connector_type,
        config={"refresh_token": "placeholder-dead-refresh"},
        schedule=None,
        is_enabled=True,
    )


def _mock_session_maker(sync_run: MagicMock) -> Any:
    """Build a mock async_sessionmaker() that yields a session with
    ``.get(SyncRun, id)`` returning the provided sync_run.

    SyncEngine uses ``async with self._session_maker() as session`` and
    inside that reads ``sync_run = await session.get(SyncRun, sync_run_id)``.
    """
    session = AsyncMock()
    session.get = AsyncMock(return_value=sync_run)
    session.commit = AsyncMock()
    # _get_last_successful_run / _get_last_pending_run call session.execute
    scalars = MagicMock()
    scalars.scalar_one_or_none = MagicMock(return_value=None)
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars)
    session.execute = AsyncMock(return_value=exec_result)

    @asynccontextmanager
    async def _ctx() -> Any:
        yield session

    return MagicMock(side_effect=_ctx), session


@pytest.mark.asyncio
async def test_oauth_reconnect_required_marks_run_auth_error() -> None:
    """Adapter raises OAuthReconnectRequiredError → sync_run + report_sync_status use AUTH_ERROR.

    Guards the sole wiring contract between the OAuth-adapter layer and
    the portal connector-status surface: without this translation the
    UI never sees the reconnect affordance and users are stuck.
    """
    sync_run = MagicMock()
    sync_run.status = SyncStatus.PENDING
    sync_run.cursor_state = None
    sync_run.error_details = None
    sync_run.quality_status = None

    session_maker_mock, _session = _mock_session_maker(sync_run)

    portal_client = MagicMock()
    portal_client.get_connector_config = AsyncMock(return_value=_portal_config())
    portal_client.report_sync_status = AsyncMock()

    # Adapter whose first call inside the try-block raises the typed error.
    # run_sync calls get_cursor_state first (line ~195 in sync_engine.py).
    adapter = MagicMock()
    adapter.get_cursor_state = AsyncMock(
        side_effect=OAuthReconnectRequiredError(
            "Microsoft refresh_token rejected (connector_id=conn-uuid-reconnect): "
            "AADSTS700082: refresh token expired"
        ),
    )

    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)

    ingest_client = MagicMock()
    ingest_client.ingest = AsyncMock()
    crawl_sync_client = MagicMock()

    engine = SyncEngine(
        session_maker=session_maker_mock,
        registry=registry,
        ingest_client=ingest_client,
        portal_client=portal_client,
        settings=MagicMock(),
        image_store=None,
        crawl_sync_client=crawl_sync_client,
    )

    # Act: run a sync that will hit the OAuthReconnectRequiredError path.
    connector_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    sync_run_id = uuid.UUID("87654321-4321-8765-4321-876543218765")
    await engine.run_sync(connector_id, sync_run_id)

    # Assert: the sync_run object gets AUTH_ERROR status
    assert sync_run.status == SyncStatus.AUTH_ERROR, (
        f"expected AUTH_ERROR status on sync_run, got {sync_run.status}"
    )

    # Assert: report_sync_status is called with AUTH_ERROR
    portal_client.report_sync_status.assert_awaited_once()
    kwargs = portal_client.report_sync_status.await_args.kwargs
    assert kwargs["sync_status"] == SyncStatus.AUTH_ERROR
    assert kwargs["connector_id"] == connector_id
    assert kwargs["sync_run_id"] == sync_run_id

    # Assert: error_details carries the reconnect_required reason so downstream
    # consumers can distinguish this from a generic auth failure.
    error_details = kwargs.get("error_details") or []
    assert any(
        isinstance(e, dict) and e.get("reason") == "reconnect_required"
        for e in error_details
    ), f"expected reason=reconnect_required in error_details, got {error_details!r}"


@pytest.mark.asyncio
async def test_generic_exception_falls_through_to_failed_not_auth_error() -> None:
    """A non-OAuth exception still ends up as FAILED, not AUTH_ERROR.

    Regression guard: the except-clause ordering matters. If someone
    accidentally moves ``except OAuthReconnectRequiredError`` *after*
    ``except Exception``, every OAuth reconnect would silently become
    FAILED and the Reconnect UI never surfaces.
    """
    sync_run = MagicMock()
    sync_run.status = SyncStatus.PENDING
    sync_run.cursor_state = None
    sync_run.error_details = None
    sync_run.quality_status = None

    session_maker_mock, _session = _mock_session_maker(sync_run)

    portal_client = MagicMock()
    portal_client.get_connector_config = AsyncMock(return_value=_portal_config())
    portal_client.report_sync_status = AsyncMock()

    adapter = MagicMock()
    adapter.get_cursor_state = AsyncMock(side_effect=RuntimeError("unrelated boom"))

    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)

    engine = SyncEngine(
        session_maker=session_maker_mock,
        registry=registry,
        ingest_client=MagicMock(ingest=AsyncMock()),
        portal_client=portal_client,
        settings=MagicMock(),
        image_store=None,
        crawl_sync_client=MagicMock(),
    )

    await engine.run_sync(
        uuid.UUID("12345678-1234-5678-1234-567812345678"),
        uuid.UUID("87654321-4321-8765-4321-876543218765"),
    )

    portal_client.report_sync_status.assert_awaited_once()
    kwargs = portal_client.report_sync_status.await_args.kwargs
    assert kwargs["sync_status"] == SyncStatus.FAILED, (
        "generic exception must not be treated as AUTH_ERROR — except ordering matters"
    )
