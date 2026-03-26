"""Tests for metadata allowlist in qdrant_store.py (TASK-007)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.qdrant_store import search


def _make_search_result(payload: dict, score: float = 0.9):
    """Create a mock Qdrant search result."""
    result = MagicMock()
    result.payload = payload
    result.score = score
    return result


@pytest.mark.asyncio
async def test_metadata_does_not_contain_user_id():
    """user_id must NOT appear in metadata (V008)."""
    mock_result = _make_search_result({
        "text": "some text",
        "kb_slug": "personal",
        "path": "note.md",
        "org_id": "org1",
        "user_id": "secret-user-123",
        "chunk_index": 0,
        "title": "My Note",
    })

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client:
        mock_client.return_value.search = AsyncMock(return_value=[mock_result])
        results = await search("org1", [0.1] * 1024)

    assert len(results) == 1
    assert "user_id" not in results[0]["metadata"]


@pytest.mark.asyncio
async def test_metadata_does_not_contain_org_id():
    """org_id must NOT appear in metadata."""
    mock_result = _make_search_result({
        "text": "some text",
        "kb_slug": "org",
        "path": "doc.md",
        "org_id": "org1",
        "chunk_index": 0,
    })

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client:
        mock_client.return_value.search = AsyncMock(return_value=[mock_result])
        results = await search("org1", [0.1] * 1024)

    assert "org_id" not in results[0]["metadata"]


@pytest.mark.asyncio
async def test_metadata_contains_allowed_fields():
    """Allowed fields should appear in metadata."""
    mock_result = _make_search_result({
        "text": "some text",
        "kb_slug": "org",
        "path": "doc.md",
        "org_id": "org1",
        "chunk_index": 3,
        "title": "Test Title",
        "created_at": "2026-01-01",
        "user_id": "leak-me-not",
    })

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client:
        mock_client.return_value.search = AsyncMock(return_value=[mock_result])
        results = await search("org1", [0.1] * 1024)

    meta = results[0]["metadata"]
    assert meta["title"] == "Test Title"
    assert meta["kb_slug"] == "org"
    assert meta["chunk_index"] == 3
    assert meta["created_at"] == "2026-01-01"
    assert "user_id" not in meta
    assert "org_id" not in meta
    assert "text" not in meta
    assert "path" not in meta


@pytest.mark.asyncio
async def test_search_with_user_id_filter():
    """When user_id and personal kb_slugs provided, filter should be applied."""
    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client:
        mock_search = AsyncMock(return_value=[])
        mock_client.return_value.search = mock_search
        await search("org1", [0.1] * 1024, kb_slugs=["personal"], user_id="user123")

        call_kwargs = mock_search.call_args
        query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")
        # Should have 3 must conditions: org_id, kb_slug, user_id
        assert len(query_filter.must) == 3


@pytest.mark.asyncio
async def test_search_without_user_id_no_extra_filter():
    """Without user_id, only org_id filter should be applied."""
    with patch("knowledge_ingest.qdrant_store.get_client") as mock_client:
        mock_search = AsyncMock(return_value=[])
        mock_client.return_value.search = mock_search
        await search("org1", [0.1] * 1024)

        call_kwargs = mock_search.call_args
        query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")
        # Should have only 1 must condition: org_id
        assert len(query_filter.must) == 1
