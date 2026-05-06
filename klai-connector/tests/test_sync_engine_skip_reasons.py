"""SPEC-INGEST-RECONCILE-001 — sync_engine skip_reasons persistence.

Acceptance criteria covered:

- AC-6: ``connector.sync_runs.skip_reasons`` is populated by
  ``_execute_sync`` with ``{reason_code: count}`` for persist-stage
  drops.
- AC-7: ``documents_ok = documents_total - documents_failed -
  sum(skip_reasons.values())`` (corrected arithmetic).
- AC-8 (mechanism): a synthetic short doc increments
  ``skip_reasons["content_too_short"]`` and excludes it from
  ``documents_ok``.

Tests run the existing _execute_sync code path with mocked adapters
and assert on the SyncRun model state and portal report payload — the
two operator-facing surfaces of the SPEC.
"""

# ruff: noqa: S106  -- placeholder tokens for test config

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.base import DocumentRef
from app.core.enums import SyncStatus
from app.reason_codes import PersistSkipReason
from app.services.portal_client import PortalConnectorConfig
from app.services.sync_engine import SyncEngine


def _portal_config() -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id="conn-skip-reasons",
        kb_id=42,
        kb_slug="support",
        zitadel_org_id="org-test",
        connector_type="notion",
        config={"token": "placeholder"},
        schedule=None,
        is_enabled=True,
    )


def _mock_session_maker(sync_run: MagicMock) -> tuple[Any, AsyncMock]:
    session = AsyncMock()
    session.get = AsyncMock(return_value=sync_run)
    session.commit = AsyncMock()

    # ``_get_last_successful_run`` / ``_get_last_pending_run`` use
    # ``result.scalars().first()`` — return a result where the scalar
    # iterator yields nothing (no prior run).
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=None)
    scalars.scalar_one_or_none = MagicMock(return_value=None)
    exec_result = MagicMock()
    exec_result.scalars = MagicMock(return_value=scalars)
    session.execute = AsyncMock(return_value=exec_result)

    @asynccontextmanager
    async def _ctx() -> Any:
        yield session

    return MagicMock(side_effect=_ctx), session


def _short_doc_ref(path: str = "/short.md") -> DocumentRef:
    return DocumentRef(
        path=path,
        ref=path,
        size=4,  # tiny content; size is metadata only, doesn't gate the ingest path.
        content_type="text/markdown",
        source_ref=path,
        source_url=f"https://notion.example/{path}",
        last_edited="2026-05-06T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_short_doc_increments_skip_reasons_and_excludes_documents_ok() -> None:
    """AC-6 + AC-7 + AC-8 mechanism: short doc → skip_reasons.content_too_short = 1
    and the corrected documents_ok arithmetic excludes it from "ok"."""
    sync_run = MagicMock()
    sync_run.status = SyncStatus.PENDING
    sync_run.cursor_state = None
    sync_run.error_details = None
    sync_run.quality_status = None
    sync_run.skip_reasons = {}

    session_maker_mock, _session = _mock_session_maker(sync_run)

    portal_client = MagicMock()
    portal_client.get_connector_config = AsyncMock(return_value=_portal_config())
    portal_client.report_sync_status = AsyncMock()

    # Adapter returns a single ref; the parser will produce <50-char text
    # so the short-skip branch fires.
    adapter = MagicMock()
    adapter.get_cursor_state = AsyncMock(return_value={})
    adapter.list_documents = AsyncMock(return_value=[_short_doc_ref()])
    adapter.fetch_document = AsyncMock(return_value=b"<bytes>")
    adapter.post_sync = AsyncMock(return_value=None)

    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)

    ingest_client = MagicMock()
    ingest_client.ingest_document = AsyncMock()  # never called for short docs
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

    # Patch the parser so the engine sees text shorter than the 50-char
    # threshold without actually invoking unstructured.io.
    short_text_result = MagicMock()
    short_text_result.text = "tiny"
    short_text_result.images = []

    def _fake_parser(_content: bytes, _filename: str) -> Any:  # noqa: ANN401
        return short_text_result

    import app.services.sync_engine as engine_module

    original_parser = engine_module.parse_document_with_images
    engine_module.parse_document_with_images = _fake_parser
    try:
        await engine.run_sync(
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
            uuid.UUID("22222222-2222-2222-2222-222222222222"),
        )
    finally:
        engine_module.parse_document_with_images = original_parser

    # AC-6: skip_reasons populated with content_too_short=1.
    assert sync_run.skip_reasons == {PersistSkipReason.CONTENT_TOO_SHORT.value: 1}
    # AC-7: documents_ok excludes the short-skipped doc. With 1 short doc
    # and zero failures, documents_ok must NOT have been incremented for
    # this ref (only the resume-already-ingested branch sets it pre-loop).
    assert sync_run.documents_ok == 0
    # documents_total counts all refs that entered the loop (1 short doc).
    assert sync_run.documents_total == 1
    assert sync_run.documents_failed == 0
    # The ingest path must never be reached for short docs.
    ingest_client.ingest_document.assert_not_awaited()
    # Portal call still receives the corrected counters (consumer contract
    # in AC-7 second sentence: "Existing API consumers continue reading
    # documents_ok and get the corrected value").
    portal_client.report_sync_status.assert_awaited_once()
    kwargs = portal_client.report_sync_status.await_args.kwargs
    assert kwargs["documents_ok"] == 0
    assert kwargs["documents_total"] == 1


@pytest.mark.asyncio
async def test_no_short_docs_keeps_skip_reasons_empty() -> None:
    """Negative case: a sync with zero drops persists ``skip_reasons={}``.

    Regression guard against accidentally setting a default-non-empty
    dict that would trip the JSONB CHECK constraint or pollute
    dashboards with phantom zero-count keys.
    """
    sync_run = MagicMock()
    sync_run.status = SyncStatus.PENDING
    sync_run.cursor_state = None
    sync_run.error_details = None
    sync_run.quality_status = None
    sync_run.skip_reasons = {}

    session_maker_mock, _session = _mock_session_maker(sync_run)

    portal_client = MagicMock()
    portal_client.get_connector_config = AsyncMock(return_value=_portal_config())
    portal_client.report_sync_status = AsyncMock()

    # No refs at all — list_documents returns empty.
    adapter = MagicMock()
    adapter.get_cursor_state = AsyncMock(return_value={})
    adapter.list_documents = AsyncMock(return_value=[])
    adapter.fetch_document = AsyncMock()
    adapter.post_sync = AsyncMock(return_value=None)

    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)

    engine = SyncEngine(
        session_maker=session_maker_mock,
        registry=registry,
        ingest_client=MagicMock(),
        portal_client=portal_client,
        settings=MagicMock(),
        image_store=None,
        crawl_sync_client=MagicMock(),
    )

    await engine.run_sync(
        uuid.UUID("33333333-3333-3333-3333-333333333333"),
        uuid.UUID("44444444-4444-4444-4444-444444444444"),
    )

    assert sync_run.skip_reasons == {}
