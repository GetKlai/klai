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
    adapter.listing_is_complete = MagicMock(return_value=True)
    adapter.confirm_gone = AsyncMock(side_effect=lambda _connector, missing: set(missing))
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
    # What knowledge-ingest stores for the connector; here the previous run's refs.
    if isinstance(ingest_client, MagicMock) and not isinstance(ingest_client.list_connector_source_refs, AsyncMock):
        stored = set(previous_run.cursor_state["synced_refs"]) if previous_run else set()
        ingest_client.list_connector_source_refs = AsyncMock(return_value=stored)
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
    previous.cursor_state = {"synced_refs": [refs[0].source_ref, refs[1].source_ref, f"json-feed:{CONNECTOR_ID}:c"]}
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
async def test_shrunken_complete_listing_deletes_every_missing_ref() -> None:
    """A complete listing is the source's own statement: a shrink deletes what left.

    The former refusal below half the baseline latched forever on an intended
    shrink and kept documents outside a narrowed connector filter indexed.
    """
    previous_refs = [_ref(chr(ord("a") + index)) for index in range(10)]
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref for ref in previous_refs]}
    current = _sync_run()
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[current], adapter=_adapter(previous_refs[:2]), ingest_client=ingest_client, previous_run=previous
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    deleted = sorted(call.kwargs["source_ref"] for call in ingest_client.delete_connector_document.await_args_list)
    assert deleted == sorted(ref.source_ref for ref in previous_refs[2:])
    assert current.cursor_state["synced_refs"] == sorted(ref.source_ref for ref in previous_refs[:2])


@pytest.mark.asyncio
async def test_renamed_parts_of_an_equally_large_listing_replace_the_old_parts() -> None:
    """A json_feed whose part names all change (same size) must not keep the old parts indexed."""
    previous_refs = [_ref(chr(ord("a") + index)) for index in range(10)]
    renamed_refs = [_ref(chr(ord("k") + index)) for index in range(10)]
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref for ref in previous_refs]}
    current = _sync_run()
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock()
    engine = _engine(
        runs=[current],
        adapter=_adapter(renamed_refs),
        ingest_client=ingest_client,
        previous_run=previous,
    )

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    deleted = sorted(call.kwargs["source_ref"] for call in ingest_client.delete_connector_document.await_args_list)
    assert deleted == sorted(ref.source_ref for ref in previous_refs)
    assert current.cursor_state["synced_refs"] == sorted(ref.source_ref for ref in renamed_refs)


@pytest.mark.asyncio
async def test_partial_sync_never_deletes_stale_groups() -> None:
    """AC-6: one failed document gates every destructive reconciliation call."""
    refs = [_ref("a"), _ref("b")]
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [refs[0].source_ref, refs[1].source_ref, f"json-feed:{CONNECTOR_ID}:c"]}
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
    adapter.listing_is_complete = MagicMock(return_value=False)
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
async def test_failed_delete_completes_the_run_and_is_retried_next_sync(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed delete does not stop the run or the other deletes; the ref stays known and is retried."""
    ref = _ref("a")
    failing_ref = f"json-feed:{CONNECTOR_ID}:stale-1"
    other_ref = f"json-feed:{CONNECTOR_ID}:stale-2"
    first = _sync_run()
    second = _sync_run()
    adapter = _adapter([ref])
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()
    ingest_client.delete_connector_document = AsyncMock(
        side_effect=[RuntimeError("downstream delete failed"), True, True]
    )
    # The store still holds the ref whose delete failed, so the next run finds it again.
    ingest_client.list_connector_source_refs = AsyncMock(
        side_effect=[{ref.source_ref, failing_ref, other_ref}, {ref.source_ref, failing_ref}]
    )
    previous = MagicMock()
    previous.cursor_state = {"synced_refs": [ref.source_ref]}
    engine = _engine(runs=[first, second], adapter=adapter, ingest_client=ingest_client, previous_run=previous)

    caplog.set_level(logging.INFO)
    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert first.status == SyncStatus.COMPLETED
    assert [c.kwargs["source_ref"] for c in ingest_client.delete_connector_document.await_args_list] == [
        failing_ref,
        other_ref,
    ]
    assert first.error_details == [
        {"warning": "stale_cleanup_delete_failed", "source_ref": failing_ref, "reason": "downstream delete failed"}
    ]
    counts = next(r for r in caplog.records if getattr(r, "event", None) == "source_deletions")
    assert (counts.listed, counts.known, counts.removed, counts.kept, counts.delete_failed) == (1, 3, 1, 1, 1)

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert ingest_client.delete_connector_document.await_args_list[-1].kwargs["source_ref"] == failing_ref
    assert second.status == SyncStatus.COMPLETED


class _ContentHashDedupFake:
    """Knowledge-ingest boundary fake implementing its content-hash decision seam."""

    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}
        self.requests = 0
        self.ingests = 0

    async def ingest_document(self, *, path: str, content: str, **_kwargs: Any) -> bool:
        self.requests += 1
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        if self._hashes.get(path) == content_hash:
            return False
        self._hashes[path] = content_hash
        self.ingests += 1
        return True

    async def delete_connector_document(self, **_kwargs: Any) -> None:
        return None

    async def list_connector_source_refs(self, **_kwargs: Any) -> set[str]:
        return set()


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
    reported = [call.kwargs["documents_changed"] for call in engine._portal_client.report_sync_status.await_args_list]
    assert reported == [1, 0]


@pytest.mark.asyncio
async def test_documents_changed_counts_real_ingests_and_stale_deletes_but_not_skips() -> None:
    """The portal spends LLM calls on support reanalysis only for changed
    knowledge, so an ingest that knowledge-ingest skipped as unchanged and a
    stale delete that removed nothing must not count, while a real ingest and a
    stale delete that removed knowledge both do."""
    refs = [_ref("a"), _ref("b")]
    previous = MagicMock()
    previous.cursor_state = {
        "synced_refs": [
            refs[0].source_ref,
            refs[1].source_ref,
            f"json-feed:{CONNECTOR_ID}:c",
            f"json-feed:{CONNECTOR_ID}:d",
        ]
    }
    current = _sync_run()
    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock(side_effect=[False, True])
    ingest_client.delete_connector_document = AsyncMock(side_effect=[True, False])
    engine = _engine(runs=[current], adapter=_adapter(refs), ingest_client=ingest_client, previous_run=previous)

    await engine.run_sync(CONNECTOR_ID, uuid.uuid4())

    assert current.status == SyncStatus.COMPLETED
    engine._portal_client.report_sync_status.assert_awaited_once()
    assert engine._portal_client.report_sync_status.await_args.kwargs["documents_changed"] == 2
