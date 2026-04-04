"""Tests for _BatchSplittingEmbedder in knowledge_ingest.graph."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge_ingest.graph import _GRAPHITI_AVAILABLE

if _GRAPHITI_AVAILABLE:
    from knowledge_ingest.graph import _BatchSplittingEmbedder
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

pytestmark = pytest.mark.skipif(not _GRAPHITI_AVAILABLE, reason="graphiti-core not installed")


def _make_inner(batch_result: list[list[float]] | None = None) -> OpenAIEmbedder:
    """Create a mock OpenAIEmbedder with configurable create/create_batch."""
    inner = MagicMock(spec=OpenAIEmbedder)
    inner.config = OpenAIEmbedderConfig(
        base_url="http://tei:7997/v1",
        api_key="test",
        embedding_model="bge-m3",
        embedding_dim=3,
    )
    if batch_result is not None:
        inner.create_batch = AsyncMock(return_value=batch_result)
    inner.create = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return inner


@pytest.mark.asyncio
async def test_small_batch_no_splitting():
    """Batch smaller than limit goes through as single call."""
    embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    inner = _make_inner(batch_result=embeddings)

    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=10)

    result = await wrapper.create_batch(["hello", "world"])

    assert result == embeddings
    inner.create_batch.assert_called_once_with(["hello", "world"])


@pytest.mark.asyncio
async def test_large_batch_splits_correctly():
    """Batch exceeding limit is split into sub-batches with order preserved."""
    inner = _make_inner()
    inner.create_batch = AsyncMock(
        side_effect=[
            [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],  # sub-batch 1
            [[0.7, 0.8, 0.9]],  # sub-batch 2
        ]
    )

    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=2)

    result = await wrapper.create_batch(["a", "b", "c"])

    assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]]
    assert inner.create_batch.call_count == 2
    inner.create_batch.assert_any_call(["a", "b"])
    inner.create_batch.assert_any_call(["c"])


@pytest.mark.asyncio
async def test_sub_batch_failure_falls_back_to_individual():
    """When a sub-batch fails, items are embedded individually."""
    inner = _make_inner()
    inner.create_batch = AsyncMock(side_effect=Exception("TEI 413"))
    inner.create = AsyncMock(side_effect=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]])

    # batch_size=2 forces splitting of 3 items into 2 sub-batches
    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=2)

    result = await wrapper.create_batch(["x", "y", "z"])

    assert len(result) == 3
    assert inner.create.call_count == 3


@pytest.mark.asyncio
async def test_empty_batch_returns_empty():
    """Empty input returns empty list without calling inner."""
    inner = _make_inner()
    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=10)

    result = await wrapper.create_batch([])

    assert result == []
    inner.create_batch.assert_not_called()


@pytest.mark.asyncio
async def test_create_delegates_to_inner():
    """Single-item create() delegates directly to inner embedder."""
    inner = _make_inner()
    inner.create = AsyncMock(return_value=[0.1, 0.2, 0.3])
    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=10)

    result = await wrapper.create("hello")

    assert result == [0.1, 0.2, 0.3]
    inner.create.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_tei_info_resolves_batch_size():
    """Batch size is discovered from TEI /info endpoint."""
    inner = _make_inner(batch_result=[[0.1, 0.2, 0.3]])
    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997/v1", default_batch_size=32)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"max_client_batch_size": 128}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        batch_size = await wrapper._resolve_batch_size()

    assert batch_size == 128
    assert wrapper._resolved is True


@pytest.mark.asyncio
async def test_tei_info_failure_uses_default():
    """When /info fails, default batch size is used."""
    inner = _make_inner(batch_result=[[0.1, 0.2, 0.3]])
    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=32)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        batch_size = await wrapper._resolve_batch_size()

    assert batch_size == 32
    assert wrapper._resolved is False


@pytest.mark.asyncio
async def test_tei_info_queried_only_once():
    """After resolving, /info is not queried again."""
    inner = _make_inner(batch_result=[[0.1, 0.2, 0.3]])
    wrapper = _BatchSplittingEmbedder(inner, tei_base_url="http://tei:7997", default_batch_size=32)
    wrapper._resolved = True
    wrapper._batch_size = 64

    batch_size = await wrapper._resolve_batch_size()

    assert batch_size == 64


@pytest.mark.asyncio
async def test_url_stripping():
    """TEI base URL is normalized — /v1 suffix stripped for /info query."""
    wrapper = _BatchSplittingEmbedder(
        _make_inner(), tei_base_url="http://tei:7997/v1", default_batch_size=32
    )
    assert wrapper._tei_base_url == "http://tei:7997"

    wrapper2 = _BatchSplittingEmbedder(
        _make_inner(), tei_base_url="http://tei:7997/v1/", default_batch_size=32
    )
    assert wrapper2._tei_base_url == "http://tei:7997"

    wrapper3 = _BatchSplittingEmbedder(
        _make_inner(), tei_base_url="http://tei:7997", default_batch_size=32
    )
    assert wrapper3._tei_base_url == "http://tei:7997"
