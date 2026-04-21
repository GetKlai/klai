"""Tests for SyncEngine handling of CanaryMismatchError and quality_status.

SPEC-CRAWL-003 REQ-5, REQ-3, REQ-2: Layer A abort + quality_status on SyncRun.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.adapters.webcrawler import CanaryMismatchError
from app.core.enums import SyncStatus
from app.models.sync_run import SyncRun
from app.services.sync_engine import SyncEngine


def _make_portal_config(connector_type: str = "webcrawler") -> SimpleNamespace:
    return SimpleNamespace(
        connector_type=connector_type,
        zitadel_org_id="org-001",
        kb_slug="kb-test",
        config={
            "base_url": "https://wiki.example.com",
            "canary_url": "https://wiki.example.com/known-page",
            "canary_fingerprint": "0123456789abcdef",
        },
        allowed_assertion_modes=[],
        connector_id=uuid.uuid4(),
    )


def _make_sync_run(sync_run_id: uuid.UUID, connector_id: uuid.UUID) -> SyncRun:
    run = SyncRun()
    run.id = sync_run_id
    run.connector_id = connector_id
    run.status = SyncStatus.RUNNING
    run.started_at = datetime.now(UTC)
    run.quality_status = None
    run.error_details = None
    run.cursor_state = None
    run.documents_total = 0
    run.documents_ok = 0
    run.documents_failed = 0
    run.bytes_processed = 0
    return run


def _make_sync_engine(
    adapter_mock: MagicMock,
    session_mock: AsyncMock,
    portal_config: SimpleNamespace,
) -> SyncEngine:
    registry = MagicMock()
    registry.get.return_value = adapter_mock

    ingest_client = AsyncMock()
    portal_client = AsyncMock()
    portal_client.get_connector_config = AsyncMock(return_value=portal_config)
    portal_client.report_sync_status = AsyncMock()

    session_maker = MagicMock()
    session_maker.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

    return SyncEngine(
        session_maker=session_maker,
        registry=registry,
        ingest_client=ingest_client,
        portal_client=portal_client,
    )


class TestSyncEngineCanaryMismatch:
    """SyncEngine handles CanaryMismatchError from adapter (SPEC-CRAWL-003 REQ-5)."""

    async def test_canary_mismatch_sets_auth_error_status(self) -> None:
        """test_canary_mismatch_sets_auth_error_status — status=AUTH_ERROR when canary fails."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.list_documents = AsyncMock(
            side_effect=CanaryMismatchError(
                similarity=0.42,
                expected="0123456789abcdef",
                actual="ffffffffffffffff",
                canary_url="https://wiki.example.com/known-page",
            )
        )
        adapter_mock.get_cursor_state = AsyncMock(return_value={})

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=sync_run)
        session_mock.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None))
                )
            )
        )
        session_mock.commit = AsyncMock()

        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.status == SyncStatus.AUTH_ERROR, (
            f"Expected AUTH_ERROR, got {sync_run.status}"
        )

    async def test_canary_mismatch_sets_quality_status_failed(self) -> None:
        """test_canary_mismatch_sets_quality_status_failed — quality_status='failed' (REQ-5)."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.list_documents = AsyncMock(
            side_effect=CanaryMismatchError(
                similarity=0.30,
                expected="0123456789abcdef",
                actual="ffffffffffffffff",
                canary_url="https://wiki.example.com/known-page",
            )
        )
        adapter_mock.get_cursor_state = AsyncMock(return_value={})

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=sync_run)
        session_mock.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None))
                )
            )
        )
        session_mock.commit = AsyncMock()

        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.quality_status == "failed", (
            f"Expected quality_status='failed', got {sync_run.quality_status!r}"
        )

    async def test_canary_mismatch_populates_error_details(self) -> None:
        """test_canary_mismatch_populates_error_details — error_details matches REQ-3 shape."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.list_documents = AsyncMock(
            side_effect=CanaryMismatchError(
                similarity=0.42,
                expected="0123456789abcdef",
                actual="ffffffffffffffff",
                canary_url="https://wiki.example.com/known-page",
            )
        )
        adapter_mock.get_cursor_state = AsyncMock(return_value={})

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=sync_run)
        session_mock.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None))
                )
            )
        )
        session_mock.commit = AsyncMock()

        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.error_details is not None
        assert len(sync_run.error_details) == 1
        entry = sync_run.error_details[0]
        assert entry.get("reason") == "canary_mismatch", (
            f"Expected reason='canary_mismatch', got {entry!r}"
        )
        assert entry.get("canary_url") == "https://wiki.example.com/known-page"
        assert entry.get("expected_fingerprint") == "0123456789abcdef"
        assert entry.get("actual_fingerprint") == "ffffffffffffffff"
        assert abs(entry.get("similarity", -1) - 0.42) < 0.001

    async def test_canary_mismatch_commits_to_db(self) -> None:
        """SyncRun is committed to DB after CanaryMismatchError (not left as RUNNING)."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.list_documents = AsyncMock(
            side_effect=CanaryMismatchError(
                similarity=0.10,
                expected="0123456789abcdef",
                actual="0000000000000000",
                canary_url="https://wiki.example.com/known-page",
            )
        )
        adapter_mock.get_cursor_state = AsyncMock(return_value={})

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=sync_run)
        session_mock.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None))
                )
            )
        )
        session_mock.commit = AsyncMock()

        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert session_mock.commit.called, "session.commit() must be called after CanaryMismatchError"


class TestSyncEngineQualityStatusHealthy:
    """SyncEngine sets quality_status='healthy' on normal successful completion."""

    async def test_successful_sync_sets_quality_status_healthy(self) -> None:
        """test_successful_sync_sets_quality_status_healthy — normal sync → quality_status='healthy'."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()
        portal_config.config = {"base_url": "https://wiki.example.com"}  # no canary


        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={"last_crawl_at": "2026-04-16T00:00:00Z"})
        adapter_mock.list_documents = AsyncMock(return_value=[])
        adapter_mock.post_sync = AsyncMock()

        session_mock = AsyncMock()
        session_mock.get = AsyncMock(return_value=sync_run)
        # Two execute calls: _get_last_successful_run + _get_last_pending_run
        session_mock.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None))
                )
            )
        )
        session_mock.commit = AsyncMock()

        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.quality_status == "healthy", (
            f"Expected quality_status='healthy', got {sync_run.quality_status!r}"
        )
        assert sync_run.status == SyncStatus.COMPLETED
