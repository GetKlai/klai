"""
Tests for chunk_type flow through the enrichment pipeline.
SPEC-CRAWLER-005 REQ-03, AC-03.1, AC-03.2, EC-4.

Root cause diagnosis (see .moai/specs/SPEC-CRAWLER-005/chunk_type-diagnosis.md):
  When the LLM returns an invalid chunk_type (e.g. "" or a value outside the
  Literal set), Pydantic raises ValidationError inside enrich_chunk, which is
  caught and re-raised as EnrichmentError. The Procrastinate job is then retried
  up to max_attempts=2; after both retries fail, raw chunks remain in Qdrant
  without chunk_type.

  The fix is retry-then-fallback inside enrich_chunk:
    1. On ValidationError, retry ONCE with a strengthened prompt addendum.
    2. If retry also fails, return a fallback EnrichmentResult with
       chunk_type="reference" and emit a "crawl_chunk_type_drop" warning log
       with artifact_id, chunk_index, and raw_llm_response[:200].
    3. No EnrichmentError is raised for invalid chunk_type alone -- the chunk
       proceeds with a valid fallback value.

TDD: tests are written RED-first. They will fail until enrichment.py is fixed.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog.testing

from knowledge_ingest.enrichment import (
    EnrichedChunk,
    EnrichmentResult,
    enrich_chunk,
    enrich_chunks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(chunk_type: str, context_prefix: str = "Test prefix") -> dict:
    """Build a minimal LiteLLM chat completion response dict."""
    content = json.dumps({
        "context_prefix": context_prefix,
        "chunk_type": chunk_type,
        "questions": ["What is this about?", "How does it work?"],
    })
    return {
        "choices": [{"message": {"content": content}}]
    }


def _make_httpx_response(body: dict, status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# AC-03.1: Valid LLM response sets chunk_type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_chunk_valid_response_sets_chunk_type():
    """AC-03.1: When LLM returns valid chunk_type, EnrichmentResult carries it."""
    mock_resp = _make_httpx_response(_make_llm_response("procedural"))
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("knowledge_ingest.enrichment.httpx.AsyncClient", return_value=mock_ctx):
        result = await enrich_chunk(
            document_text="Some document text",
            chunk_text="Step 1: do this. Step 2: do that.",
            title="Test Article",
            path="/test/article",
        )

    assert result.chunk_type == "procedural"
    assert isinstance(result, EnrichmentResult)


# ---------------------------------------------------------------------------
# AC-03.2 + EC-4: Invalid chunk_type triggers retry then fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_chunk_empty_chunk_type_retries_then_falls_back():
    """AC-03.2 + EC-4: When LLM always returns empty chunk_type:
    - a retry is attempted once,
    - the fallback chunk_type is a valid Literal value ("reference"),
    - a crawl_chunk_type_drop warning log fires with required fields.
    """
    # Both LLM calls (original + retry) return invalid chunk_type = ""
    # The raw JSON is parseable but chunk_type fails the Literal validator
    invalid_response = _make_httpx_response({
        "choices": [{"message": {"content": json.dumps({
            "context_prefix": "Some prefix",
            "chunk_type": "",   # invalid -- not in Literal
            "questions": ["Q1"],
        })}}]
    })

    call_count = 0

    async def _fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return invalid_response

    mock_client = AsyncMock()
    mock_client.post = _fake_post
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("knowledge_ingest.enrichment.httpx.AsyncClient", return_value=mock_ctx),
        structlog.testing.capture_logs() as cap_logs,
    ):
        result = await enrich_chunk(
            document_text="Document body",
            chunk_text="Warning: do not press the button.",
            title="Safety Article",
            path="/safety/article",
            artifact_id="art-abc123",
            chunk_index=2,
        )

    # Fallback must produce a valid chunk_type, not raise EnrichmentError
    valid_chunk_types = {"procedural", "conceptual", "reference", "warning", "example"}
    assert result.chunk_type in valid_chunk_types, (
        f"Expected valid fallback chunk_type, got: {result.chunk_type!r}"
    )

    # Two LLM calls: original + 1 retry
    assert call_count == 2, f"Expected 2 LLM calls (original + retry), got {call_count}"

    # Warning log must fire with required fields
    warn_logs = [
        log for log in cap_logs if log.get("log_level") == "warning"
        and log.get("event") == "crawl_chunk_type_drop"
    ]
    assert len(warn_logs) == 1, (
        f"Expected exactly 1 crawl_chunk_type_drop warning, got: {warn_logs!r}"
    )
    log = warn_logs[0]
    assert log.get("artifact_id") == "art-abc123", f"Missing artifact_id in log: {log!r}"
    assert log.get("chunk_index") == 2, f"Missing chunk_index in log: {log!r}"
    assert "raw_llm_response" in log, f"Missing raw_llm_response in log: {log!r}"
    # raw_llm_response must be truncated to max 200 chars
    assert len(log["raw_llm_response"]) <= 200, (
        f"raw_llm_response not truncated: len={len(log['raw_llm_response'])}"
    )


@pytest.mark.asyncio
async def test_enrich_chunk_retry_succeeds_on_second_call():
    """AC-03.2 variant: first LLM call returns invalid chunk_type,
    retry returns a valid one. No fallback used, no warning log fired."""
    first_invalid = {
        "choices": [{"message": {"content": json.dumps({
            "context_prefix": "Prefix",
            "chunk_type": "not_a_valid_type",
            "questions": ["Q"],
        })}}]
    }
    second_valid = {
        "choices": [{"message": {"content": json.dumps({
            "context_prefix": "Corrected prefix",
            "chunk_type": "conceptual",
            "questions": ["What is X?"],
        })}}]
    }

    call_count = 0

    async def _fake_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = first_invalid if call_count == 1 else second_valid
        return resp

    mock_client = AsyncMock()
    mock_client.post = _fake_post
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("knowledge_ingest.enrichment.httpx.AsyncClient", return_value=mock_ctx),
        structlog.testing.capture_logs() as cap_logs,
    ):
        result = await enrich_chunk(
            document_text="Doc",
            chunk_text="Chunk text",
            title="Title",
            path="/path",
            artifact_id="art-xyz",
            chunk_index=0,
        )

    assert result.chunk_type == "conceptual"
    assert call_count == 2, f"Expected 2 calls, got {call_count}"

    # No warning should fire when retry succeeds
    warn_logs = [
        log for log in cap_logs if log.get("event") == "crawl_chunk_type_drop"
    ]
    assert len(warn_logs) == 0, f"Unexpected warning log fired: {warn_logs!r}"


# ---------------------------------------------------------------------------
# AC-03.1 via upsert: chunk_type in Qdrant payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_upsert_enriched_chunks_writes_chunk_type_when_valid():
    """AC-03.1: upsert_enriched_chunks puts chunk_type in the Qdrant PointStruct."""
    from qdrant_client.models import PointStruct, SparseVector

    from knowledge_ingest.qdrant_store import upsert_enriched_chunks

    captured_points: list[PointStruct] = []

    mock_client = AsyncMock()

    async def _capture_upsert(collection, points):
        captured_points.extend(points)

    mock_client.delete = AsyncMock()
    mock_client.upsert = _capture_upsert

    ec = EnrichedChunk(
        original_text="Original chunk text",
        enriched_text="Prefix\n\nOriginal chunk text",
        context_prefix="Prefix",
        questions=["Q1", "Q2"],
        chunk_type="procedural",
    )

    chunk_vec = [0.1] * 1024
    q_vec = [0.2] * 1024
    sparse_vec = SparseVector(indices=[1, 2, 3], values=[0.5, 0.6, 0.7])

    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_client):
        await upsert_enriched_chunks(
            org_id="org-test",
            kb_slug="support",
            path="/test/page",
            enriched_chunks=[ec],
            chunk_vectors=[chunk_vec],
            question_vectors=[q_vec],
            sparse_vectors=[sparse_vec],
            artifact_id="art-test-123",
            extra_payload={"source_type": "crawl"},
        )

    assert len(captured_points) == 1, f"Expected 1 point, got {len(captured_points)}"
    point = captured_points[0]
    assert point.payload.get("chunk_type") == "procedural", (
        f"chunk_type not in Qdrant payload: {point.payload}"
    )


@pytest.mark.asyncio
async def test_upsert_enriched_chunks_omits_chunk_type_when_empty():
    """Gate at qdrant_store line 275: empty chunk_type is NOT written to payload."""
    from qdrant_client.models import PointStruct

    from knowledge_ingest.qdrant_store import upsert_enriched_chunks

    captured_points: list[PointStruct] = []

    mock_client = AsyncMock()

    async def _capture_upsert(collection, points):
        captured_points.extend(points)

    mock_client.delete = AsyncMock()
    mock_client.upsert = _capture_upsert

    ec = EnrichedChunk(
        original_text="text",
        enriched_text="prefix\n\ntext",
        context_prefix="prefix",
        questions=[],
        chunk_type="",   # empty -- should NOT be written
    )

    with patch("knowledge_ingest.qdrant_store.get_client", return_value=mock_client):
        await upsert_enriched_chunks(
            org_id="org-x",
            kb_slug="kb",
            path="/p",
            enriched_chunks=[ec],
            chunk_vectors=[[0.0] * 1024],
            question_vectors=[None],
        )

    assert len(captured_points) == 1
    assert "chunk_type" not in captured_points[0].payload, (
        "chunk_type should be absent when ec.chunk_type is empty"
    )


# ---------------------------------------------------------------------------
# Pipeline integration: enrich_chunks preserves chunk_type end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_chunks_pipeline_preserves_chunk_type():
    """enrich_chunks returns EnrichedChunk instances whose chunk_type matches
    what the LLM returned (valid case)."""
    responses = [
        _make_llm_response("procedural"),
        _make_llm_response("conceptual"),
    ]
    call_idx = 0

    async def _fake_post(*args, **kwargs):
        nonlocal call_idx
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = responses[call_idx % len(responses)]
        call_idx += 1
        return resp

    mock_client = AsyncMock()
    mock_client.post = _fake_post
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("knowledge_ingest.enrichment.httpx.AsyncClient", return_value=mock_ctx):
        enriched = await enrich_chunks(
            document_text="Full document body",
            chunks=["Chunk one text", "Chunk two text"],
            title="Article",
            path="/article",
        )

    assert len(enriched) == 2
    assert enriched[0].chunk_type == "procedural"
    assert enriched[1].chunk_type == "conceptual"
    for ec in enriched:
        assert isinstance(ec, EnrichedChunk)
        valid_types = {"procedural", "conceptual", "reference", "warning", "example"}
        assert ec.chunk_type in valid_types
