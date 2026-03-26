"""Tests for knowledge_ingest/sparse_embedder.py"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge_ingest.sparse_embedder import embed_sparse


@pytest.mark.asyncio
async def test_embed_sparse_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "indices": [1, 5, 12],
        "values": [0.8, 0.3, 0.1],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            result = await embed_sparse("hello")

    assert result is not None
    assert list(result.indices) == [1, 5, 12]
    assert list(result.values) == [0.8, 0.3, 0.1]


@pytest.mark.asyncio
async def test_embed_sparse_sidecar_unreachable():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            result = await embed_sparse("hello")

    assert result is None


@pytest.mark.asyncio
async def test_embed_sparse_empty_url_returns_none():
    with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
        mock_settings.sparse_sidecar_url = ""
        result = await embed_sparse("hello")

    assert result is None


@pytest.mark.asyncio
async def test_embed_sparse_maps_response_to_sparse_vector():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "indices": [0, 42, 99],
        "values": [1.0, 0.5, 0.01],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            result = await embed_sparse("hello")

    assert result is not None
    assert len(result.indices) == 3
    assert len(result.values) == 3
    assert result.indices[1] == 42
    assert result.values[0] == 1.0
