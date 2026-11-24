"""Tests for knowledge_ingest/sparse_embedder.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge_ingest.sparse_embedder import SparseEmbeddingUnavailable, embed_sparse_batch


def _client(post: AsyncMock) -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = post
    return mock_client


def _settings(mock_settings: MagicMock, batch_size: int = 64) -> None:
    mock_settings.sparse_sidecar_url = "http://test:8001"
    mock_settings.sparse_sidecar_timeout = 5.0
    mock_settings.sparse_sidecar_batch_size = batch_size


@pytest.mark.asyncio
async def test_embed_sparse_batch_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"indices": [1, 2], "values": [0.5, 0.3]},
            {"indices": [3, 4, 5], "values": [0.9, 0.7, 0.1]},
        ],
    }
    post = AsyncMock(return_value=mock_response)

    with (
        patch("httpx.AsyncClient", return_value=_client(post)),
        patch("knowledge_ingest.sparse_embedder.settings") as mock_settings,
    ):
        _settings(mock_settings)
        results = await embed_sparse_batch(["hello", "world"])

    assert [list(r.indices) for r in results] == [[1, 2], [3, 4, 5]]
    assert [list(r.values) for r in results] == [[0.5, 0.3], [0.9, 0.7, 0.1]]
    assert "/embed_sparse_batch" in post.call_args[0][0]
    assert post.call_args[1]["json"] == {"texts": ["hello", "world"]}


@pytest.mark.asyncio
async def test_embed_sparse_batch_splits_large_input():
    """Texts exceeding batch_size should be split into multiple sub-batches."""
    first, second = MagicMock(), MagicMock()
    first.json.return_value = {"results": [{"indices": [i], "values": [0.5]} for i in range(2)]}
    second.json.return_value = {"results": [{"indices": [99], "values": [0.1]}]}
    post = AsyncMock(side_effect=[first, second])

    with (
        patch("httpx.AsyncClient", return_value=_client(post)),
        patch("knowledge_ingest.sparse_embedder.settings") as mock_settings,
    ):
        _settings(mock_settings, batch_size=2)
        results = await embed_sparse_batch(["a", "b", "c"])

    assert [list(r.indices) for r in results] == [[0], [1], [99]]
    assert post.await_count == 2  # 2 sub-batches: [a,b] and [c]


@pytest.mark.asyncio
async def test_an_unreachable_sidecar_raises_after_its_retries_and_asks_no_further_batch():
    post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    with (
        patch("httpx.AsyncClient", return_value=_client(post)),
        patch("knowledge_ingest.sparse_embedder.settings") as mock_settings,
        patch("knowledge_ingest.sparse_embedder.asyncio.sleep", AsyncMock()),
        pytest.raises(SparseEmbeddingUnavailable),
    ):
        _settings(mock_settings, batch_size=2)
        await embed_sparse_batch(["a", "b", "c", "d", "e"])

    assert post.await_count == 3  # three tries of the first batch, none of the others


@pytest.mark.asyncio
async def test_an_unconfigured_sidecar_raises():
    with (
        patch("knowledge_ingest.sparse_embedder.settings") as mock_settings,
        pytest.raises(SparseEmbeddingUnavailable),
    ):
        mock_settings.sparse_sidecar_url = ""
        await embed_sparse_batch(["a"])


@pytest.mark.asyncio
async def test_a_result_count_mismatch_raises_instead_of_shifting_vectors():
    """Fewer results than texts must not shift vectors onto the wrong chunks."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": [{"indices": [1], "values": [0.5]}]}

    with (
        patch("httpx.AsyncClient", return_value=_client(AsyncMock(return_value=mock_response))),
        patch("knowledge_ingest.sparse_embedder.settings") as mock_settings,
        patch("knowledge_ingest.sparse_embedder.asyncio.sleep", AsyncMock()),
        pytest.raises(SparseEmbeddingUnavailable),
    ):
        _settings(mock_settings)
        await embed_sparse_batch(["a", "b"])
