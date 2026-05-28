"""Tests for SPEC-INGEST-CONTENT-PG-001 (audit finding 1).

Pin the contract:
- ``read_artifact_for_enrichment`` returns the full row + parsed extra
  JSONB, or None when the artifact is missing.
- ``_load_and_enrich`` skips silently when the artifact is missing or
  has no ``document_text`` on extra.
- ``_load_and_enrich`` re-derives chunks + parents from the *current*
  ``extra.document_text``, not from any frozen task arg.
- Procrastinate task variants accept only ``artifact_id``; all other
  fields flow through PG.

SPEC-TI-003-FOLLOWUP-001: ``read_artifact_for_enrichment`` now takes
``conn`` as its first argument; ``_load_and_enrich`` opens a
``cross_org_admin_connection`` to look up the org_id from artifact_id.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Procrastinate stub — same pattern as test_ingest_enrichment_dedup.py
# ---------------------------------------------------------------------------


class _AlreadyEnqueued(Exception):
    """Stub for procrastinate.exceptions.AlreadyEnqueued."""


def _install_procrastinate_stub() -> None:
    if "procrastinate" in sys.modules:
        return
    exceptions_mod = types.ModuleType("procrastinate.exceptions")
    exceptions_mod.AlreadyEnqueued = _AlreadyEnqueued  # type: ignore[attr-defined]
    pkg = types.ModuleType("procrastinate")
    pkg.exceptions = exceptions_mod  # type: ignore[attr-defined]
    sys.modules["procrastinate"] = pkg
    sys.modules["procrastinate.exceptions"] = exceptions_mod


_install_procrastinate_stub()


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


# ---------------------------------------------------------------------------
# read_artifact_for_enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_artifact_returns_none_for_missing():
    conn = _make_mock_conn()
    conn.fetchrow = AsyncMock(return_value=None)

    from knowledge_ingest.pg_store import read_artifact_for_enrichment

    result = await read_artifact_for_enrichment(conn, "00000000-0000-0000-0000-000000000000")

    assert result is None


@pytest.mark.asyncio
async def test_read_artifact_returns_none_for_empty_id():
    """Defensive: empty/None id short-circuits without a DB hit."""
    conn = _make_mock_conn()

    from knowledge_ingest.pg_store import read_artifact_for_enrichment

    assert await read_artifact_for_enrichment(conn, "") is None
    conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_read_artifact_returns_dict_with_parsed_extra():
    """JSONB extra is returned as a dict, not as a JSON string."""
    fake_row = {
        "id": "11111111-2222-3333-4444-555555555555",
        "org_id": "org1",
        "kb_slug": "kb1",
        "path": "docs/page.md",
        "user_id": None,
        "content_type": "kb_article",
        "synthesis_depth": 1,
        "assertion_mode": "factual",
        "provenance_type": "extracted",
        "confidence": None,
        "belief_time_start": 0,
        "belief_time_end": 253402300800,
        "extra": '{"document_text": "# Hi", "title": "Hi", "tags": ["onboarding"]}',
    }
    conn = _make_mock_conn()
    conn.fetchrow = AsyncMock(return_value=fake_row)

    from knowledge_ingest.pg_store import read_artifact_for_enrichment

    result = await read_artifact_for_enrichment(conn, "11111111-2222-3333-4444-555555555555")

    assert result is not None
    assert result["org_id"] == "org1"
    assert result["kb_slug"] == "kb1"
    assert result["path"] == "docs/page.md"
    assert result["content_type"] == "kb_article"
    assert result["synthesis_depth"] == 1
    # extra MUST be a dict, not a JSON string
    assert isinstance(result["extra"], dict)
    assert result["extra"]["document_text"] == "# Hi"
    assert result["extra"]["title"] == "Hi"
    assert result["extra"]["tags"] == ["onboarding"]


@pytest.mark.asyncio
async def test_read_artifact_handles_dict_extra():
    """Some asyncpg deployments return JSONB as native dict already."""
    fake_row = {
        "id": "11111111-2222-3333-4444-555555555555",
        "org_id": "org1",
        "kb_slug": "kb1",
        "path": "docs/page.md",
        "user_id": None,
        "content_type": "kb_article",
        "synthesis_depth": 1,
        "assertion_mode": "factual",
        "provenance_type": "extracted",
        "confidence": None,
        "belief_time_start": 0,
        "belief_time_end": 253402300800,
        "extra": {"document_text": "body", "title": "T"},
    }
    conn = _make_mock_conn()
    conn.fetchrow = AsyncMock(return_value=fake_row)

    from knowledge_ingest.pg_store import read_artifact_for_enrichment

    result = await read_artifact_for_enrichment(conn, "11111111-2222-3333-4444-555555555555")

    assert isinstance(result["extra"], dict)
    assert result["extra"]["document_text"] == "body"


# ---------------------------------------------------------------------------
# _load_and_enrich — soft-skip + delegation contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_and_enrich_skips_when_artifact_missing():
    """Connector-purge or upstream race deleted the artifact: soft-skip."""
    with (
        patch(
            "knowledge_ingest.enrichment_tasks.pg_store.read_artifact_for_enrichment",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "knowledge_ingest.enrichment_tasks._enrich_document", new_callable=AsyncMock
        ) as mock_enrich,
    ):
        from knowledge_ingest.enrichment_tasks import _load_and_enrich

        await _load_and_enrich("00000000-0000-0000-0000-000000000000")

    mock_enrich.assert_not_called()


@pytest.mark.asyncio
async def test_load_and_enrich_skips_when_document_text_missing():
    """Legacy artifact rows without document_text are not enrichable
    via this path; rebuild_kb is the right tool. Soft-skip with a log
    event, no exception.
    """
    fake_artifact = {
        "artifact_id": "11111111-2222-3333-4444-555555555555",
        "org_id": "org1",
        "kb_slug": "kb1",
        "path": "docs/page.md",
        "user_id": None,
        "content_type": "kb_article",
        "synthesis_depth": 0,
        "assertion_mode": "factual",
        "provenance_type": "extracted",
        "confidence": None,
        "belief_time_start": 0,
        "belief_time_end": 253402300800,
        "extra": {"title": "Hi"},  # no document_text!
    }
    with (
        patch(
            "knowledge_ingest.enrichment_tasks.pg_store.read_artifact_for_enrichment",
            new_callable=AsyncMock,
            return_value=fake_artifact,
        ),
        patch(
            "knowledge_ingest.enrichment_tasks._enrich_document", new_callable=AsyncMock
        ) as mock_enrich,
        patch(
            "knowledge_ingest.enrichment_tasks._set_direct_upload_index_status",
            new_callable=AsyncMock,
        ) as mock_status,
    ):
        from knowledge_ingest.enrichment_tasks import _load_and_enrich

        await _load_and_enrich("11111111-2222-3333-4444-555555555555")

    mock_enrich.assert_not_called()
    mock_status.assert_awaited_once_with(fake_artifact, "failed")


@pytest.mark.asyncio
async def test_load_and_enrich_skips_truncated_docling_artifact():
    """Existing queued jobs for pre-chunked large Docling files soft-skip."""
    fake_artifact = {
        "artifact_id": "11111111-2222-3333-4444-555555555555",
        "org_id": "org1",
        "kb_slug": "chemie",
        "path": "file:sha256:source",
        "user_id": None,
        "content_type": "document",
        "synthesis_depth": 0,
        "assertion_mode": "factual",
        "provenance_type": "extracted",
        "confidence": None,
        "belief_time_start": 0,
        "belief_time_end": 253402300800,
        "extra": {
            "document_text": "Preview only",
            "document_text_truncated": True,
            "docling_chunk_count": 1557,
        },
    }
    with (
        patch(
            "knowledge_ingest.enrichment_tasks.pg_store.read_artifact_for_enrichment",
            new_callable=AsyncMock,
            return_value=fake_artifact,
        ),
        patch(
            "knowledge_ingest.enrichment_tasks._enrich_document", new_callable=AsyncMock
        ) as mock_enrich,
        patch(
            "knowledge_ingest.enrichment_tasks._set_direct_upload_index_status",
            new_callable=AsyncMock,
        ) as mock_status,
    ):
        from knowledge_ingest.enrichment_tasks import _load_and_enrich

        await _load_and_enrich("11111111-2222-3333-4444-555555555555")

    mock_enrich.assert_not_called()
    mock_status.assert_awaited_once_with(fake_artifact, "synced")


@pytest.mark.asyncio
async def test_load_and_enrich_passes_pg_state_to_enrich_document():
    """The kwargs handed to _enrich_document come entirely from PG —
    not from any caller-frozen state. This is the contract that closes
    the audit-finding-1 race window.
    """
    fake_artifact = {
        "artifact_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "org_id": "org-pg",
        "kb_slug": "kb-pg",
        "path": "docs/p.md",
        "user_id": "user-1",
        "content_type": "kb_article",
        "synthesis_depth": 2,
        "assertion_mode": "factual",
        "provenance_type": "extracted",
        "confidence": None,
        "belief_time_start": 0,
        "belief_time_end": 253402300800,
        "extra": {
            "document_text": "# Title\n\nSome body.\n\n## Section\n\nMore body.\n",
            "title": "Title",
            "tags": ["onboarding"],
            "source_type": "upload",
        },
    }

    captured_kwargs: dict = {}

    async def _fake_enrich(**kwargs) -> None:
        captured_kwargs.update(kwargs)

    with (
        patch(
            "knowledge_ingest.enrichment_tasks.pg_store.read_artifact_for_enrichment",
            new_callable=AsyncMock,
            return_value=fake_artifact,
        ),
        patch("knowledge_ingest.enrichment_tasks._enrich_document", side_effect=_fake_enrich),
    ):
        from knowledge_ingest.enrichment_tasks import _load_and_enrich

        await _load_and_enrich("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    # The downstream call MUST receive the PG-sourced fields verbatim
    assert captured_kwargs["org_id"] == "org-pg"
    assert captured_kwargs["kb_slug"] == "kb-pg"
    assert captured_kwargs["path"] == "docs/p.md"
    assert captured_kwargs["user_id"] == "user-1"
    assert captured_kwargs["synthesis_depth"] == 2
    assert captured_kwargs["content_type"] == "kb_article"
    assert captured_kwargs["title"] == "Title"
    assert captured_kwargs["artifact_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    # document_text is the canonical body that the worker re-chunks
    assert "# Title" in captured_kwargs["document_text"]
    assert "## Section" in captured_kwargs["document_text"]

    # chunks list is non-empty and derived from the body
    assert isinstance(captured_kwargs["chunks"], list)
    assert len(captured_kwargs["chunks"]) >= 1
    # Parents should also be derived
    assert isinstance(captured_kwargs["parents"], list)
    assert isinstance(captured_kwargs["parent_index_per_child"], list)
    assert len(captured_kwargs["parent_index_per_child"]) == len(captured_kwargs["chunks"])

    # extra_payload is the *current* PG extra — not a frozen task arg
    assert captured_kwargs["extra_payload"]["tags"] == ["onboarding"]
    assert captured_kwargs["extra_payload"]["source_type"] == "upload"


# ---------------------------------------------------------------------------
# Smoke test on the task variants — confirm signature accepts only artifact_id
# ---------------------------------------------------------------------------


def test_task_variants_have_single_arg_signature():
    """SPEC-INGEST-CONTENT-PG-001: the public task signature must be
    ``(artifact_id: str)`` — anything else means task-args still carry
    content and the audit finding 1 race regression is back.
    """
    import inspect

    from knowledge_ingest import enrichment_tasks

    sig = inspect.signature(enrichment_tasks._load_and_enrich)
    params = list(sig.parameters.values())
    assert len(params) == 1, f"_load_and_enrich must take 1 param, got {params}"
    assert params[0].name == "artifact_id"
