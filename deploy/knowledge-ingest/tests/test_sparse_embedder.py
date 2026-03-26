"""Tests for knowledge_ingest/sparse_embedder.py"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge_ingest.sparse_embedder import embed_sparse, embed_sparse_batch


# --- embed_sparse (single text, delegates to batch) ---


@pytest.mark.asyncio
async def test_embed_sparse_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [{"indices": [1, 5, 12], "values": [0.8, 0.3, 0.1]}],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 64
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
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 64
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
        "results": [{"indices": [0, 42, 99], "values": [1.0, 0.5, 0.01]}],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 64
            result = await embed_sparse("hello")

    assert result is not None
    assert len(result.indices) == 3
    assert len(result.values) == 3
    assert result.indices[1] == 42
    assert result.values[0] == 1.0


# --- embed_sparse_batch ---


@pytest.mark.asyncio
async def test_embed_sparse_batch_success():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"indices": [1, 2], "values": [0.5, 0.3]},
            {"indices": [3, 4, 5], "values": [0.9, 0.7, 0.1]},
        ],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 64
            results = await embed_sparse_batch(["hello", "world"])

    assert len(results) == 2
    assert results[0] is not None
    assert list(results[0].indices) == [1, 2]
    assert list(results[0].values) == [0.5, 0.3]
    assert results[1] is not None
    assert list(results[1].indices) == [3, 4, 5]
    assert list(results[1].values) == [0.9, 0.7, 0.1]


@pytest.mark.asyncio
async def test_embed_sparse_batch_sidecar_unreachable():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 64
            results = await embed_sparse_batch(["hello", "world"])

    assert len(results) == 2
    assert results[0] is None
    assert results[1] is None


@pytest.mark.asyncio
async def test_embed_sparse_batch_empty_url_returns_nones():
    with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
        mock_settings.sparse_sidecar_url = ""
        results = await embed_sparse_batch(["a", "b", "c"])

    assert len(results) == 3
    assert all(r is None for r in results)


@pytest.mark.asyncio
async def test_embed_sparse_delegates_to_batch():
    """embed_sparse("hello") should call the batch endpoint, not the single endpoint."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [{"indices": [10], "values": [0.9]}],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 64
            result = await embed_sparse("hello")

    # Verify it called the batch endpoint
    call_args = mock_client.post.call_args
    assert "/embed_sparse_batch" in call_args[0][0]
    assert call_args[1]["json"] == {"texts": ["hello"]}
    assert result is not None
    assert list(result.indices) == [10]


@pytest.mark.asyncio
async def test_embed_sparse_batch_splits_large_input():
    """Texts exceeding batch_size should be split into multiple sub-batches."""
    mock_response_1 = MagicMock()
    mock_response_1.status_code = 200
    mock_response_1.raise_for_status = MagicMock()
    mock_response_1.json.return_value = {
        "results": [{"indices": [i], "values": [0.5]} for i in range(2)],
    }
    mock_response_2 = MagicMock()
    mock_response_2.status_code = 200
    mock_response_2.raise_for_status = MagicMock()
    mock_response_2.json.return_value = {
        "results": [{"indices": [99], "values": [0.1]}],
    }

    call_count = 0

    async def post_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_response_1
        return mock_response_2

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=post_side_effect)
        mock_client_cls.return_value = mock_client

        with patch("knowledge_ingest.sparse_embedder.settings") as mock_settings:
            mock_settings.sparse_sidecar_url = "http://test:8001"
            mock_settings.sparse_sidecar_timeout = 5.0
            mock_settings.sparse_sidecar_batch_size = 2  # Force split at 2
            results = await embed_sparse_batch(["a", "b", "c"])

    assert len(results) == 3
    assert results[0] is not None
    assert results[1] is not None
    assert results[2] is not None
    assert list(results[2].indices) == [99]
    assert call_count == 2  # 2 sub-batches: [a,b] and [c]
