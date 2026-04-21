"""Tests for SyncEngine Layer C: post-sync boilerplate cluster detection.

SPEC-CRAWL-003 REQ-13, REQ-14, REQ-17.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.adapters.base import DocumentRef
from app.core.enums import SyncStatus
from app.models.sync_run import SyncRun
from app.services.sync_engine import SyncEngine


def _make_portal_config() -> SimpleNamespace:
    return SimpleNamespace(
        connector_type="webcrawler",
        zitadel_org_id="org-001",
        kb_slug="kb-test",
        config={"base_url": "https://wiki.example.com"},
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


def _make_ref_with_fingerprint(url: str, fp: str) -> DocumentRef:
    """Create a DocumentRef with a specific fingerprint, skipping content fetch."""
    return DocumentRef(
        path=url.split("/", 3)[-1] + ".md",
        ref=url,
        size=100,
        content_type="kb_article",
        source_ref=url,
        source_url=url,
        content_fingerprint=fp,
    )


class TestLayerCBoilerplateDetection:
    """Layer C: post-sync boilerplate cluster detection (SPEC-CRAWL-003 REQ-13)."""

    async def test_boilerplate_cluster_sets_quality_status_degraded(self) -> None:
        """test_boilerplate_cluster_sets_quality_status_degraded — >15% near-dups → 'degraded'."""
        # 30 pages: 25 with identical fingerprint (login wall), 5 with distinct fingerprints.
        # Ratio = 25/30 = 0.83 > 0.15 threshold → degraded.
        login_wall_fp = "aaaaaaaaaaaaaaaa"
        boilerplate_refs = [
            _make_ref_with_fingerprint(f"https://wiki.example.com/wall-{i}", login_wall_fp)
            for i in range(25)
        ]
        # Use Fibonacci-spread distinct fingerprints to avoid accidental clustering
        prime = 0x9E3779B97F4A7C15
        distinct_refs = [
            _make_ref_with_fingerprint(
                f"https://wiki.example.com/ok-{i}",
                f"{((prime * (i + 1)) & 0xFFFFFFFFFFFFFFFF):016x}",
            )
            for i in range(5)
        ]
        all_refs = boilerplate_refs + distinct_refs

        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={})
        adapter_mock.list_documents = AsyncMock(return_value=all_refs)
        # fetch_document raises so documents_ok stays 0 but refs are returned
        adapter_mock.fetch_document = AsyncMock(side_effect=Exception("skip"))
        adapter_mock.post_sync = AsyncMock()

        session_mock = _make_session_mock(sync_run)
        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.quality_status == "degraded", (
            f"Expected quality_status='degraded', got {sync_run.quality_status!r}"
        )

    async def test_no_cluster_keeps_quality_status_healthy(self) -> None:
        """No boilerplate cluster → quality_status stays 'healthy'."""
        prime = 0x9E3779B97F4A7C15
        distinct_refs = [
            _make_ref_with_fingerprint(
                f"https://wiki.example.com/ok-{i}",
                f"{((prime * (i + 1)) & 0xFFFFFFFFFFFFFFFF):016x}",
            )
            for i in range(30)
        ]

        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={})
        adapter_mock.list_documents = AsyncMock(return_value=distinct_refs)
        adapter_mock.fetch_document = AsyncMock(side_effect=Exception("skip"))
        adapter_mock.post_sync = AsyncMock()

        session_mock = _make_session_mock(sync_run)
        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.quality_status == "healthy", (
            f"Expected quality_status='healthy', got {sync_run.quality_status!r}"
        )

    async def test_layer_c_skipped_when_fewer_than_30_pages(self) -> None:
        """test_layer_c_skipped_when_fewer_than_30_pages — <30 pages exempt from Layer C (REQ-14)."""
        # 20 pages all identical fingerprint — would trigger if Layer C ran, but it should not
        login_wall_fp = "bbbbbbbbbbbbbbbb"
        small_refs = [
            _make_ref_with_fingerprint(f"https://wiki.example.com/wall-{i}", login_wall_fp)
            for i in range(20)
        ]

        connector_id = uuid.uuid4()
        sync_run_id = uuid.uuid4()
        sync_run = _make_sync_run(sync_run_id, connector_id)
        portal_config = _make_portal_config()

        adapter_mock = MagicMock()
        adapter_mock.get_cursor_state = AsyncMock(return_value={})
        adapter_mock.list_documents = AsyncMock(return_value=small_refs)
        adapter_mock.fetch_document = AsyncMock(side_effect=Exception("skip"))
        adapter_mock.post_sync = AsyncMock()

        session_mock = _make_session_mock(sync_run)
        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        # Layer C skipped: quality_status should be 'healthy' (not 'degraded')
        assert sync_run.quality_status == "healthy", (
            f"Expected quality_status='healthy' for <30 page sync, got {sync_run.quality_status!r}"
        )

    async def test_layer_c_status_remains_completed_when_degraded(self) -> None:
        """SyncRun.status stays COMPLETED even when quality_status='degraded' (REQ-13)."""
        login_wall_fp = "cccccccccccccccc"
        boilerplate_refs = [
            _make_ref_with_fingerprint(f"https://wiki.example.com/wall-{i}", login_wall_fp)
            for i in range(25)
        ]
        prime = 0x9E3779B97F4A7C15
        distinct_refs = [
            _make_ref_with_fingerprint(
                f"https://wiki.example.com/ok-{i}",
                f"{((prime * (i + 1)) & 0xFFFFFFFFFFFFFFFF):016x}",
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
        engine = _make_sync_engine(adapter_mock, session_mock, portal_config)
        await engine._execute_sync(connector_id, sync_run_id)

        assert sync_run.status == SyncStatus.COMPLETED, (
            f"Expected status=COMPLETED, got {sync_run.status}"
        )
        assert sync_run.quality_status == "degraded"
