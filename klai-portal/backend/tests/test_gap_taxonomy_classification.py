"""Tests for SPEC-KB-026 R4 part 2: gap taxonomy classification via knowledge-ingest.

Tests classify_gap_taxonomy client function and the wiring in create_gap_event.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestClassifyGapTaxonomy:
    """R4/2: classify_gap_taxonomy calls knowledge-ingest classify endpoint."""

    @pytest.mark.asyncio
    async def test_classify_returns_node_ids_on_success(self):
        """Successful call returns list of taxonomy node IDs."""
        from app.services.knowledge_ingest_client import classify_gap_taxonomy

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"taxonomy_node_ids": [5, 7]})
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
            result = await classify_gap_taxonomy("org1", "kb1", "How do I pay?")

        assert result == [5, 7]
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["text"] == "How do I pay?"

    @pytest.mark.asyncio
    async def test_classify_returns_empty_on_error(self):
        """On HTTP error, returns empty list (best-effort)."""
        from app.services.knowledge_ingest_client import classify_gap_taxonomy

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
            result = await classify_gap_taxonomy("org1", "kb1", "test query")

        assert result == []

    @pytest.mark.asyncio
    async def test_classify_returns_empty_on_timeout(self):
        """On timeout, returns empty list."""
        from app.services.knowledge_ingest_client import classify_gap_taxonomy

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=TimeoutError())

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client):
            result = await classify_gap_taxonomy("org1", "kb1", "test query")

        assert result == []

    @pytest.mark.asyncio
    async def test_classify_uses_correct_headers(self):
        """Must use X-Internal-Secret header (not x-internal-token)."""
        from app.services.knowledge_ingest_client import classify_gap_taxonomy

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"taxonomy_node_ids": []})
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("app.services.knowledge_ingest_client.httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await classify_gap_taxonomy("org1", "kb1", "test")

        # Check that AsyncClient was called with headers containing X-Internal-Secret
        client_call = mock_cls.call_args
        headers = client_call.kwargs.get("headers", {})
        assert "X-Internal-Secret" in headers
