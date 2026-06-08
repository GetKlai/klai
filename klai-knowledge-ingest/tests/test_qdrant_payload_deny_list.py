"""Tests for audit-2026-05-06 finding 4: Qdrant chunk-payload deny-list.

`extra_payload` carries document_text, document_summary and document_language
through the Procrastinate task args because:
- `rebuild_tasks._reconstruct_document_text` reads document_text from
  `knowledge.artifacts.extra` (PG, not Qdrant) — that path is fine.
- `_enrich_document` reads document_summary from `extra_payload` for
  cache hits across Procrastinate retries.

But for a 100 KB markdown with 50 chunks, copying the body into every
chunk's Qdrant payload costs ~5 MB per document. At 100k+ docs this
crosses 250 GB of dead weight (RAM at InMemory mode, SSD at OnDisk).
The read-side `_ALLOWED_METADATA_FIELDS` filter already drops the
fields when search() returns to retrieval-api, confirming nobody
consumes them out of Qdrant.

These tests pin the deny-list contract: stripping at the Qdrant
boundary is permitted because it's the single Qdrant write site for
both Phase-1 raw chunks and Phase-2 enriched chunks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.qdrant_store import (
    _QDRANT_PAYLOAD_DENY_LIST,
    _extra_payload_for_qdrant,
    upsert_chunks,
    upsert_enriched_chunks,
)

# ---------------------------------------------------------------------------
# Pure unit tests on the filter helper
# ---------------------------------------------------------------------------


def test_deny_list_contains_expected_keys():
    """Pin the contract: any code that adds a key here must also update
    the consumer-side reads in _enrich_document and rebuild_tasks.
    """
    assert _QDRANT_PAYLOAD_DENY_LIST == frozenset(
        {"document_text", "document_summary", "document_language"}
    )


def test_filter_returns_empty_dict_for_none():
    assert _extra_payload_for_qdrant(None) == {}


def test_filter_returns_empty_dict_for_empty():
    assert _extra_payload_for_qdrant({}) == {}


def test_filter_passes_non_deny_keys_through():
    extra = {"title": "My Doc", "tags": ["a", "b"], "visibility": "internal"}
    assert _extra_payload_for_qdrant(extra) == extra


def test_filter_strips_deny_list_keys():
    extra = {
        "title": "My Doc",
        "document_text": "huge body" * 1000,
        "document_summary": "concise summary",
        "document_language": "nl",
        "tags": ["a"],
    }
    result = _extra_payload_for_qdrant(extra)
    assert result == {"title": "My Doc", "tags": ["a"]}
    assert "document_text" not in result
    assert "document_summary" not in result
    assert "document_language" not in result


def test_filter_does_not_mutate_input():
    """Defensive — returning a new dict, not modifying the caller's
    extra_payload (which is also a Procrastinate task arg).
    """
    extra = {"title": "x", "document_text": "huge"}
    snapshot = dict(extra)
    _extra_payload_for_qdrant(extra)
    assert extra == snapshot


# ---------------------------------------------------------------------------
# Integration: full upsert path strips deny-list before Qdrant write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_chunks_strips_deny_list_from_qdrant_payload():
    """Phase-1 raw upsert must NOT persist document_text / document_summary
    / document_language in chunk payload, but must keep all other keys.
    """
    extra_payload = {
        "title": "Doc Title",
        "source_label": "Notion · Voys Sales",
        "content_label": ["faq", "billing"],
        "visibility": "internal",
        # Deny-list — must NOT reach Qdrant payload
        "document_text": "the full document body" * 500,
        "document_summary": "a concise contextual-retrieval summary",
        "document_language": "nl",
    }

    captured_points = []

    async def _capture_upsert(*_args, points=None, **_kwargs):
        if points is not None:
            captured_points.extend(points)

    mock_client = MagicMock()
    mock_client.delete = AsyncMock(return_value=None)
    mock_client.upsert = AsyncMock(side_effect=_capture_upsert)

    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_client):
        await upsert_chunks(
            org_id="org1",
            kb_slug="kb1",
            path="docs/page.md",
            chunks=["chunk one", "chunk two"],
            vectors=[[0.1] * 10, [0.2] * 10],
            artifact_id="artifact-uuid-1",
            extra_payload=extra_payload,
        )

    assert len(captured_points) == 2, "expected one point per chunk"
    for point in captured_points:
        # Deny-list MUST be absent
        assert "document_text" not in point.payload, (
            "document_text leaked into Qdrant payload — finding 4 regression. "
            "If you intentionally added it back, also update consumers in "
            "retrieval-api and the read-side _ALLOWED_METADATA_FIELDS filter."
        )
        assert "document_summary" not in point.payload
        assert "document_language" not in point.payload

        # All other keys MUST survive
        assert point.payload.get("title") == "Doc Title"
        assert point.payload.get("source_label") == "Notion · Voys Sales"
        assert point.payload.get("content_label") == ["faq", "billing"]
        assert point.payload.get("visibility") == "internal"

        # And the always-on fields must still be set
        assert point.payload["org_id"] == "org1"
        assert point.payload["kb_slug"] == "kb1"
        assert point.payload["path"] == "docs/page.md"
        assert point.payload["artifact_id"] == "artifact-uuid-1"


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_strips_deny_list_from_qdrant_payload():
    """Phase-2 enriched upsert must enforce the same deny-list. Drift
    between the two upsert paths is exactly the regression class that
    finding 4 documents.
    """

    class _FakeEnrichedChunk:
        def __init__(self, original_text: str, enriched_text: str) -> None:
            self.original_text = original_text
            self.enriched_text = enriched_text
            self.context_prefix = "context"
            self.questions = ["q1", "q2"]

    extra_payload = {
        "title": "Doc Title",
        "tags": ["onboarding"],
        "visibility": "private",
        # Deny-list
        "document_text": "huge body" * 500,
        "document_summary": "summary",
        "document_language": "en",
    }

    captured_points = []

    async def _capture_upsert(*_args, points=None, **_kwargs):
        if points is not None:
            captured_points.extend(points)

    mock_client = MagicMock()
    mock_client.delete = AsyncMock(return_value=None)
    mock_client.upsert = AsyncMock(side_effect=_capture_upsert)

    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_client):
        await upsert_enriched_chunks(
            org_id="org1",
            kb_slug="kb1",
            path="docs/page.md",
            enriched_chunks=[
                _FakeEnrichedChunk("orig 1", "enriched 1"),
                _FakeEnrichedChunk("orig 2", "enriched 2"),
            ],
            chunk_vectors=[[0.1] * 10, [0.2] * 10],
            question_vectors=[None, None],
            sparse_vectors=None,
            artifact_id="artifact-uuid-2",
            extra_payload=extra_payload,
            content_type="kb_article",
        )

    assert len(captured_points) == 2
    for point in captured_points:
        assert "document_text" not in point.payload
        assert "document_summary" not in point.payload
        assert "document_language" not in point.payload

        assert point.payload.get("title") == "Doc Title"
        assert point.payload.get("tags") == ["onboarding"]
        assert point.payload.get("visibility") == "private"

        # Phase-2 specific fields still present
        assert "text" in point.payload
        assert "text_enriched" in point.payload
        assert "context_prefix" in point.payload
        assert "questions" in point.payload
