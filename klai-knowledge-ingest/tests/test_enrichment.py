"""Tests for knowledge_ingest/enrichment.py"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge_ingest.enrichment import enrich_chunk, enrich_chunks


SAMPLE_RESULT = {
    "context_prefix": "Dit document beschrijft het retourbeleid van Acme.",
    "questions": [
        "Hoe lang is de retourperiode?",
        "Kan ik een product terugsturen na 30 dagen?",
        "Wat zijn de voorwaarden voor retourneren?",
    ],
}


def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(data)}}]
    }
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
async def test_enrich_chunk_success():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(SAMPLE_RESULT))
        mock_client_cls.return_value = mock_client

        result = await enrich_chunk(
            document_text="Een document over retouren.",
            chunk_text="De retourperiode is 30 dagen.",
            title="Retourbeleid",
            path="help/retour.md",
        )

    assert result is not None
    assert result.context_prefix == SAMPLE_RESULT["context_prefix"]
    assert len(result.questions) == 3


@pytest.mark.asyncio
async def test_enrich_chunk_timeout_returns_none():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        result = await enrich_chunk("doc", "chunk", "title", "path.md")

    assert result is None


@pytest.mark.asyncio
async def test_enrich_chunk_invalid_json_returns_none():
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "not valid json {"}}]}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        mock_client_cls.return_value = mock_client

        result = await enrich_chunk("doc", "chunk", "title", "path.md")

    assert result is None


@pytest.mark.asyncio
async def test_enrich_chunk_http_error_returns_none():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock()))
        mock_client_cls.return_value = mock_client

        result = await enrich_chunk("doc", "chunk", "title", "path.md")

    assert result is None


@pytest.mark.asyncio
async def test_enrich_chunks_fallback_on_failure():
    """Chunks that fail enrichment return original text without crashing."""
    chunks = ["chunk A", "chunk B"]

    with patch("knowledge_ingest.enrichment.enrich_chunk") as mock_enrich:
        # First chunk fails, second succeeds
        mock_enrich.side_effect = [
            None,
            MagicMock(
                context_prefix="Prefix B.",
                questions=["Q1?", "Q2?"],
            ),
        ]

        results = await enrich_chunks("doc text", chunks, "title", "path.md")

    assert len(results) == 2

    # Failed chunk falls back to original
    assert results[0].original_text == "chunk A"
    assert results[0].enriched_text == "chunk A"
    assert results[0].context_prefix == ""
    assert results[0].questions == []

    # Successful chunk is enriched
    assert results[1].original_text == "chunk B"
    assert results[1].enriched_text == "Prefix B.\n\nchunk B"
    assert results[1].context_prefix == "Prefix B."


@pytest.mark.asyncio
async def test_enrich_chunks_semaphore_limits_concurrency():
    """Verify semaphore is applied (calls complete, not deadlocked)."""
    chunks = [f"chunk {i}" for i in range(10)]
    calls: list[int] = []

    async def fake_enrich(*args, **kwargs):
        calls.append(1)
        return MagicMock(context_prefix="prefix", questions=["q?"])

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=fake_enrich):
        results = await enrich_chunks("doc", chunks, "title", "path.md")

    assert len(results) == 10
    assert len(calls) == 10
