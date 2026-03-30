"""Tests for reranker service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from retrieval_api.services.reranker import rerank


def _make_candidates(n: int) -> list[dict]:
    return [
        {"chunk_id": f"c{i}", "text": f"Chunk {i} text", "score": 0.9 - i * 0.1}
        for i in range(n)
    ]


class TestReranker:
    @pytest.mark.asyncio
    async def test_reranker_success(self):
        """Successful reranking sorts by reranker_score."""
        candidates = _make_candidates(3)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"index": 2, "score": 0.95},
                {"index": 0, "score": 0.85},
                {"index": 1, "score": 0.75},
            ]
        }

        with patch("retrieval_api.services.reranker.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await rerank("test query", candidates, top_k=2)

        assert len(result) == 2
        # Highest reranker_score first
        assert result[0]["reranker_score"] == 0.95
        assert result[0]["chunk_id"] == "c2"
        assert result[1]["reranker_score"] == 0.85
        assert result[1]["chunk_id"] == "c0"

    @pytest.mark.asyncio
    async def test_reranker_timeout_fallback(self):
        """On timeout, falls back to original order with reranker_score=None."""
        candidates = _make_candidates(5)

        with patch("retrieval_api.services.reranker.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.TimeoutException("timeout")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await rerank("test query", candidates, top_k=3)

        assert len(result) == 3
        for r in result:
            assert r["reranker_score"] is None
        # Preserves original order
        assert result[0]["chunk_id"] == "c0"

    @pytest.mark.asyncio
    async def test_reranker_http_error_fallback(self):
        """On HTTP error, falls back to original order."""
        candidates = _make_candidates(3)

        with patch("retrieval_api.services.reranker.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "500", request=httpx.Request("POST", "http://test"), response=httpx.Response(500)
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await rerank("test query", candidates, top_k=2)

        assert len(result) == 2
        for r in result:
            assert r["reranker_score"] is None

    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        """Empty candidate list returns empty list."""
        result = await rerank("test", [], top_k=5)
        assert result == []
