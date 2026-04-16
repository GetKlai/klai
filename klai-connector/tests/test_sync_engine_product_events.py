"""Tests for SyncEngine product event emission on quality_status transitions.

SPEC-CRAWL-003 REQ-15: knowledge.sync_quality_degraded event on quality transitions.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.adapters.base import DocumentRef
from app.adapters.webcrawler import CanaryMismatchError
from app.core.enums import SyncStatus
from app.models.sync_run import SyncRun
from app.services.sync_engine import SyncEngine


def _make_portal_config(extra_config: dict | None = None) -> SimpleNamespace:
    config = {"base_url": "https://wiki.example.com"}
    if extra_config:
        config.update(extra_config)
    return SimpleNamespace(
        connector_type="webcrawler",
        zitadel_org_id="org-001",
        kb_slug="kb-test",
        config=config,
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


def _make_session_mock(sync_run: SyncRun) -> AsyncMock:
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
    return session_mock


def _make_sync_engine_with_portal(
    adapter_mock: MagicMock,
    session_mock: AsyncMock,
    portal_config: SimpleNamespace,
) -> tuple[SyncEngine, AsyncMock]:
    """Create engine and return (engine, portal_client_mock) for assertion."""
    registry = MagicMock()
    registry.get.return_value = adapter_mock

    ingest_client = AsyncMock()
    portal_client = AsyncMock()
    portal_client.get_connector_config = AsyncMock(return_value=portal_config)
    portal_client.report_sync_status = AsyncMock()
    portal_client.report_quality_event = AsyncMock()

    session_maker = MagicMock()
    session_maker.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    session_maker.return_value.__aexit__ = AsyncMock(return_value=None)

    engine = SyncEngine(
        session_maker=session_maker,
        registry=registry,
        ingest_client=ingest_client,
        portal_client=portal_client,
    )
    return engine, portal_client


class TestProductEventOnQualityTransition:
    """REQ-15: knowledge.sync_quality_degraded emitted when quality_status transitions."""

    async def test_quality_event_emitted_on_canary_failure(self) -> None:
        """test_quality_event_emitted_on_canary_failure — canary mismatch triggers product event."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config({
            "canary_url": "https://wiki.example.com/known-page",
            "canary_fingerprint": "0123456789abcdef",
        })

        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={})
        adapter_mock.list_documents = AsyncMock(
            side_effect=CanaryMismatchError(
                similarity=0.42,
                expected="0123456789abcdef",
                actual="ffffffffffffffff",
                canary_url="https://wiki.example.com/known-page",
            )
        )

        session_mock = _make_session_mock(sync_run)
        engine, portal_client = _make_sync_engine_with_portal(
            adapter_mock, session_mock, portal_config
        )
        await engine._execute_sync(connector_id, sync_run_id)

        assert portal_client.report_quality_event.called, (
            "report_quality_event must be called on canary mismatch"
        )
        call_kwargs = portal_client.report_quality_event.call_args[1]
        assert call_kwargs.get("quality_status") == "failed"
        assert call_kwargs.get("reason") == "canary_mismatch"

    async def test_quality_event_emitted_on_degraded(self) -> None:
        """test_quality_event_emitted_on_degraded — boilerplate cluster triggers product event."""
        prime = 0x9E3779B97F4A7C15
        login_wall_fp = "aaaaaaaaaaaaaaaa"
        boilerplate_refs = [
            DocumentRef(
                path=f"wall-{i}.md",
                ref=f"https://wiki.example.com/wall-{i}",
                size=100,
                content_type="kb_article",
                source_ref=f"https://wiki.example.com/wall-{i}",
                source_url=f"https://wiki.example.com/wall-{i}",
                content_fingerprint=login_wall_fp,
            )
            for i in range(25)
        ]
        distinct_refs = [
            DocumentRef(
                path=f"ok-{i}.md",
                ref=f"https://wiki.example.com/ok-{i}",
                size=100,
                content_type="kb_article",
                source_ref=f"https://wiki.example.com/ok-{i}",
                source_url=f"https://wiki.example.com/ok-{i}",
                content_fingerprint=f"{((prime * (i + 1)) & 0xFFFFFFFFFFFFFFFF):016x}",
            )
            for i in range(5)
        ]

        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={})
        adapter_mock.list_documents = AsyncMock(return_value=boilerplate_refs + distinct_refs)
        adapter_mock.fetch_document = AsyncMock(side_effect=Exception("skip"))
        adapter_mock.post_sync = AsyncMock()

        session_mock = _make_session_mock(sync_run)
        engine, portal_client = _make_sync_engine_with_portal(
            adapter_mock, session_mock, portal_config
        )
        await engine._execute_sync(connector_id, sync_run_id)

        assert portal_client.report_quality_event.called, (
            "report_quality_event must be called on boilerplate cluster detection"
        )
        call_kwargs = portal_client.report_quality_event.call_args[1]
        assert call_kwargs.get("quality_status") == "degraded"
        assert call_kwargs.get("reason") == "boilerplate_cluster"

    async def test_no_quality_event_on_healthy_sync(self) -> None:
        """No quality event emitted when sync completes with quality_status='healthy'."""
        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={})
        adapter_mock.list_documents = AsyncMock(return_value=[])
        adapter_mock.post_sync = AsyncMock()

        session_mock = _make_session_mock(sync_run)
        engine, portal_client = _make_sync_engine_with_portal(
            adapter_mock, session_mock, portal_config
        )
        await engine._execute_sync(connector_id, sync_run_id)

        assert not portal_client.report_quality_event.called, (
            "report_quality_event must NOT be called for healthy syncs"
        )
