"""REQ-5/REQ-7 integration tests for JSON-feed sync reconciliation."""

from __future__ import annotations

import hashlib
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.base import DocumentRef
from app.core.enums import SyncStatus
from app.services.portal_client import PortalConnectorConfig
from app.services.sync_engine import SyncEngine

CONNECTOR_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _portal_config() -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id=str(CONNECTOR_ID),
        kb_id=42,
        kb_slug="prices",
        zitadel_org_id="org-test",
        connector_type="json_feed",
        config={"url": "https://data.example.com/prices.json"},
        schedule=None,
        is_enabled=True,
    )


def _ref(group: str, *, records: int = 1) -> DocumentRef:
    source_ref = f"json-feed:{CONNECTOR_ID}:{group}"
    return DocumentRef(
        path=f"json-feed/{CONNECTOR_ID}/{group}",
        ref=source_ref,
        size=100,
        content_type="kb_article",
        source_ref=source_ref,
        source_url="https://data.example.com",
        extra={
            "json_feed_group": {"category": group},
            "json_feed_record_count": records,
        },
    )


def _sync_run() -> MagicMock:
    run = MagicMock()
    run.status = SyncStatus.PENDING
    run.cursor_state = None
    run.error_details = None
    run.quality_status = None
    run.skip_reasons = {}
    return run


def _session_maker(*runs: MagicMock) -> Any:
    pending = iter(runs)

    @asynccontextmanager
    async def _ctx() -> Any:
        session = AsyncMock()
        session.get = AsyncMock(return_value=next(pending))
        session.commit = AsyncMock()
        yield session

    return _ctx


def _adapter(refs: list[DocumentRef]) -> MagicMock:
    adapter = MagicMock()
    adapter.stale_ref_cleanup_enabled = True
    adapter.get_cursor_state = AsyncMock(side_effect=lambda _connector: {})
    adapter.list_documents = AsyncMock(return_value=refs)
    adapter.fetch_document = AsyncMock(return_value=b"rendered feed document")
    adapter.get_sync_metrics = MagicMock(
        return_value={
            "groups_total": len(refs),
            "records_total": sum(ref.extra.get("json_feed_record_count", 0) for ref in refs),
            "duplicates_collapsed": 1,
        }
    )
    adapter.post_sync = AsyncMock(return_value=None)
    return adapter


def _engine(
    *,
    runs: list[MagicMock],
    adapter: MagicMock,
    ingest_client: Any,
    previous_run: MagicMock | None,
) -> SyncEngine:
    portal_client = MagicMock()
    portal_client.get_connector_config = AsyncMock(return_value=_portal_config())
    portal_client.report_sync_status = AsyncMock()
    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)
    engine = SyncEngine(
        session_maker=_session_maker(*runs),
        registry=registry,
        ingest_client=ingest_client,
        portal_client=portal_client,
        settings=MagicMock(),
        image_store=None,
        crawl_sync_client=MagicMock(),
    )
    engine._get_last_successful_run = AsyncMock(return_value=previous_run)  # type: ignore[method-assign]
    engine._get_last_pending_run = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return engine


@pytest.fixture(autouse=True)
def _parse_feed_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = MagicMock()
    parsed.text = "This rendered JSON-feed document is long enough for ingest. " * 2
    parsed.images = []
    monkeypatch.setattr(
        "app.services.sync_engine.parse_document_with_images",
        lambda _content, _filename: parsed,
    )


@pytest.mark.asyncio
async def test_successful_sync_deletes_stale_group_and_forwards_extra(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-6 + REQ-4 + REQ-7: cleanup is scoped and observable after success."""
    refs = [_ref("a"), _ref("b", records=2)]
    previous = MagicMock()
    previous.cursor_state = {
        "synced_refs": [refs[0].source_ref, refs[1].source_ref, f"json-feed:{CONNECTOR_ID}:c"]
    }
    current = _sync_run()
    adapter = _adapter(refs)
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[current],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    caplog.set_level(logging.INFO)
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert current.status == SyncStatus.COMPLETED
    ingest_client.delete_connector_document.assert_awaited_once_with(
        org_id="org-test",
        kb_slug="prices",
        source_connector_id=str(CONNECTOR_ID),
        source_ref=f"json-feed:{CONNECTOR_ID}:c",
    )
    assert [call.kwargs["document_extra"] for call in ingest_client.ingest_document.await_args_list] == [
        refs[0].extra,
        refs[1].extra,
    ]
    assert current.cursor_state["synced_refs"] == sorted(ref.source_ref for ref in refs)

    complete = next(record for record in caplog.records if getattr(record, "event", None) == "sync_complete")
    assert complete.groups_total == 2
    assert complete.records_total == 3
    assert complete.duplicates_collapsed == 1
    assert complete.stale_groups_deleted == 1


@pytest.mark.asyncio
async def test_shrunken_listing_refuses_cleanup_and_preserves_baseline_for_recovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A transient subset cannot erase the KB and a later full listing still reconciles."""
    previous_refs = [_ref(chr(ord("a") + index)) for index in range(10)]
    subset_refs = previous_refs[:2]
    recovered_refs = previous_refs[:-1]
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref for ref in previous_refs]}
    refused_run = _sync_run()
    recovered_run = _sync_run()
    adapter = _adapter(subset_refs)
    adapter.list_documents = AsyncMock(side_effect=[subset_refs, recovered_refs])
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[refused_run, recovered_run],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    caplog.set_level(logging.INFO)
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert refused_run.status == SyncStatus.COMPLETED
    ingest_client.delete_connector_document.assert_not_awaited()
    assert refused_run.cursor_state["synced_refs"] == sorted(
        ref.source_ref for ref in previous_refs
    )
    refused = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "stale_cleanup_refused_shrink"
    )
    assert refused.connector_id == str(CONNECTOR_ID)
    assert refused.previous_ref_count == 10
    assert refused.current_ref_count == 2
    complete = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "sync_complete"
    )
    assert complete.stale_cleanup_refused is True

    caplog.clear()
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    ingest_client.delete_connector_document.assert_awaited_once_with(
        org_id="org-test",
        kb_slug="prices",
        source_connector_id=str(CONNECTOR_ID),
        source_ref=previous_refs[-1].source_ref,
    )
    assert recovered_run.cursor_state["synced_refs"] == sorted(
        ref.source_ref for ref in recovered_refs
    )


@pytest.mark.asyncio
async def test_partial_sync_never_deletes_stale_groups() -> None:
    """AC-6: one failed document gates every destructive reconciliation call."""
    refs = [_ref("a"), _ref("b")]
    previous = MagicMock()
    previous.cursor_state = {
        "synced_refs": [refs[0].source_ref, refs[1].source_ref, f"json-feed:{CONNECTOR_ID}:c"]
    }
    current = _sync_run()
    adapter = _adapter(refs)
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock(side_effect=[RuntimeError("ingest failed"), None])
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[current],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert current.status == SyncStatus.FAILED
    ingest_client.delete_connector_document.assert_not_awaited()
    assert "synced_refs" not in current.cursor_state


@pytest.mark.asyncio
async def test_adapters_without_explicit_opt_in_never_delete_missing_refs() -> None:
    """REQ-5 safety gate: complete-snapshot semantics must be explicit per adapter."""
    ref = _ref("a")
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref, "provider:missing"]}
    current = _sync_run()
    adapter = _adapter([ref])
    adapter.stale_ref_cleanup_enabled = False
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[current],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert current.status == SyncStatus.COMPLETED
    ingest_client.delete_connector_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_single_document_ref_is_deleted_on_first_group_sync() -> None:
    """AC-6 migration: the WP1 legacy source_ref is stale on the first new sync."""
    current_ref = _ref("category-a")
    legacy_ref = f"json-feed:{CONNECTOR_ID}"
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [legacy_ref]}
    current = _sync_run()
    adapter = _adapter([current_ref])
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[current],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    ingest_client.delete_connector_document.assert_awaited_once_with(
        org_id="org-test",
        kb_slug="prices",
        source_connector_id=str(CONNECTOR_ID),
        source_ref=legacy_ref,
    )
    assert current.status == SyncStatus.COMPLETED


@pytest.mark.asyncio
async def test_cleanup_failure_fails_run_and_retries_from_successful_baseline() -> None:
    """REQ-7: cleanup failure is visible and the same ref is retried next sync."""
    ref = _ref("a")
    stale_ref = f"json-feed:{CONNECTOR_ID}:stale"
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref, stale_ref]}
    first = _sync_run()
    second = _sync_run()
    adapter = _adapter([ref])
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock(
        side_effect=[RuntimeError("downstream delete failed"), None]
    )
    engine = _engine(
        runs=[first, second],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert first.status == SyncStatus.FAILED
    assert first.error_details == [
        {
            "error": "Stale source_ref cleanup failed",
            "source_ref": stale_ref,
            "reason": "downstream delete failed",
        }
    ]
    assert "synced_refs" not in first.cursor_state
    assert second.status == SyncStatus.COMPLETED
    assert ingest_client.delete_connector_document.await_count == 2


class _ContentHashDedupFake:
    """Knowledge-ingest boundary fake implementing its content-hash decision seam."""

    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}
        self.requests = 0
        self.ingests = 0

    async def ingest_document(self, *, path: str, content: str, **_kwargs: Any) -> None:
        self.requests += 1
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        if self._hashes.get(path) == content_hash:
            return
        self._hashes[path] = content_hash
        self.ingests += 1

    async def delete_connector_document(self, **_kwargs: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_two_unchanged_syncs_reach_dedup_but_create_one_ingest() -> None:
    """AC-4: refetch-always reaches content-hash dedup and creates no duplicate."""
    ref = _ref("a")
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref]}
    first = _sync_run()
    second = _sync_run()
    adapter = _adapter([ref])
    ingest_client = _ContentHashDedupFake()
    engine = _engine(
        runs=[first, second],
        adapter=adapter,
        ingest_client=ingest_client,
        previous_run=previous,
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert ingest_client.requests == 2
    assert ingest_client.ingests == 1
    assert first.status == SyncStatus.COMPLETED
    assert second.status == SyncStatus.COMPLETED
