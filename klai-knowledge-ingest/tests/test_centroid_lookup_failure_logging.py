"""
Tests for audit-2026-05-06 finding 5: centroid_lookup_failed must log at
``warning`` level with structured ``org_id`` + ``kb_slug`` fields, not at
``debug`` (which is below the production INFO floor in
``logging_setup.py`` and therefore invisible to VictoriaLogs).

The centroid-classification fast-path is wrapped in a broad ``except
Exception``. If it ever silently logs at debug, every taxonomy-classified
document falls through to the expensive ``classify_document`` LLM path
without any operator-visible signal.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from knowledge_ingest.models import IngestRequest


class _MockNode:
    """Stand-in for a taxonomy node row with an ``.id`` attribute."""

    def __init__(self, node_id: int) -> None:
        self.id = node_id


def _make_request() -> IngestRequest:
    return IngestRequest(
        org_id="org-finding5",
        kb_slug="kb-finding5",
        path="docs/page.md",
        content="# Hello\nWorld",
        source_type="docs",
        content_type="kb_article",
    )


@pytest.mark.asyncio
async def test_centroid_lookup_failure_logs_at_warning_with_structured_fields():
    """When ``load_centroids`` raises, the ingest path must log a
    ``centroid_lookup_failed`` event at ``warning`` level (not debug),
    with ``org_id`` and ``kb_slug`` bound as structured fields, and then
    fall through to ``classify_document`` (centroid_matched stays False).
    """
    req = _make_request()

    mock_pool = MagicMock()
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(return_value=None)

    # Force the centroid path to be entered: KB has taxonomy nodes.
    taxonomy_nodes = [_MockNode(1), _MockNode(2)]

    with (
        patch(
            "knowledge_ingest.pg_store.get_active_content_hash",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "knowledge_ingest.pg_store.soft_delete_artifact",
            new_callable=AsyncMock,
        ),
        patch(
            "knowledge_ingest.pg_store.create_artifact",
            new_callable=AsyncMock,
            return_value="artifact-uuid-finding5",
        ),
        patch(
            "knowledge_ingest.pg_store.set_artifact_ingest_status",
            new_callable=AsyncMock,
            return_value={"artifact_id": "artifact-uuid-finding5", "path": req.path},
        ),
        patch(
            "knowledge_ingest.embedder.embed",
            new_callable=AsyncMock,
            return_value=[[0.1] * 10],
        ),
        patch(
            "knowledge_ingest.qdrant_store.upsert_chunks",
            new_callable=AsyncMock,
        ),
        patch(
            "knowledge_ingest.org_config.is_enrichment_enabled",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "knowledge_ingest.routes.ingest.kb_config.get_kb_visibility",
            new_callable=AsyncMock,
            return_value="internal",
        ),
        # SPEC-TI-003-FOLLOWUP-001 routed pool acquisition through
        # ``tenant_scoped_connection`` -- ``routes/ingest.py`` no longer
        # imports ``get_pool`` directly, so patching that path raises
        # AttributeError. Connection injection is handled by the autouse
        # ``_mock_db_helpers`` fixture in tests/conftest.py.
        patch(
            "knowledge_ingest.routes.ingest.fetch_taxonomy_nodes",
            new_callable=AsyncMock,
            return_value=taxonomy_nodes,
        ),
        patch(
            "knowledge_ingest.routes.ingest.load_centroids",
            side_effect=RuntimeError("simulated centroid blob corruption"),
        ),
        patch(
            "knowledge_ingest.routes.ingest.classify_document",
            new_callable=AsyncMock,
            return_value=([], []),  # (matched_nodes, llm_tags)
        ) as mock_classify,
        patch(
            "knowledge_ingest.routes.ingest.generate_content_label",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("knowledge_ingest.routes.ingest.settings") as mock_settings,
    ):
        mock_settings.graphiti_enabled = False
        mock_settings.chunk_size = 1500
        mock_settings.chunk_overlap = 200
        mock_settings.enrichment_enabled = False
        mock_settings.taxonomy_centroid_match_threshold = 0.7

        from knowledge_ingest.routes.ingest import ingest_document

        # SPEC-TI-003-FOLLOWUP-001: ingest_document now takes (conn, req).
        conn = MagicMock()
        conn.execute = AsyncMock(return_value=None)
        conn.fetch = AsyncMock(return_value=[])
        # ``insert_parent_chunks`` (commit 57b5040ec "Index parent chunks
        # before enrichment") runs in the ingest path and does
        # ``row_id = await conn.fetchval("... RETURNING id")``; it raises
        # RuntimeError if that returns None. Real Postgres always returns the
        # generated id, so the mock must too — returning 1 mirrors the
        # ``RETURNING id`` contract. (The centroid fast-path under test does
        # not read ``fetchval`` itself; it only needs the ingest path to
        # complete so the warning event is emitted.)
        conn.fetchval = AsyncMock(return_value=1)
        conn.fetchrow = AsyncMock(return_value=None)

        with structlog.testing.capture_logs() as captured:
            result = await ingest_document(conn, req)

    assert result["status"] == "ok"

    # The fast-path failure MUST surface as a single warning event with the
    # structured fields and a stack trace (exc_info=True).
    failure_events = [e for e in captured if e.get("event") == "centroid_lookup_failed"]
    assert len(failure_events) == 1, (
        f"expected exactly one centroid_lookup_failed event, got "
        f"{len(failure_events)}: {failure_events}"
    )
    event = failure_events[0]
    assert event["log_level"] == "warning", (
        f"centroid_lookup_failed must log at warning, got "
        f"log_level={event.get('log_level')!r}. Reason: VictoriaLogs is "
        f"configured with INFO floor; debug events never reach the log "
        f"pipeline."
    )
    assert event.get("org_id") == "org-finding5", (
        f"org_id must be bound as a structured field, got {event!r}"
    )
    assert event.get("kb_slug") == "kb-finding5", (
        f"kb_slug must be bound as a structured field, got {event!r}"
    )

    # And the LLM fallback must have been invoked, since centroid_matched
    # stayed False after the exception.
    mock_classify.assert_awaited_once()
