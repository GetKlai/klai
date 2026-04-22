"""Tests for knowledge_ingest/enrichment.py"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from knowledge_ingest.enrichment import EnrichmentError, enrich_chunk, enrich_chunks


SAMPLE_RESULT = {
    "context_prefix": "Dit document beschrijft het retourbeleid van Acme.",
    "chunk_type": "procedural",
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
async def test_enrich_chunk_timeout_raises():
    """LLM timeout must raise EnrichmentError (fail-loudly, no silent fallback)."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(EnrichmentError):
            await enrich_chunk("doc", "chunk", "title", "path.md")


@pytest.mark.asyncio
async def test_enrich_chunk_invalid_json_raises():
    """Unparseable LLM response must raise EnrichmentError (fail-loudly)."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "not valid json {"}}]}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(EnrichmentError):
            await enrich_chunk("doc", "chunk", "title", "path.md")


@pytest.mark.asyncio
async def test_enrich_chunk_http_error_raises():
    """HTTP error must raise EnrichmentError (fail-loudly)."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock()))
        mock_client_cls.return_value = mock_client

        with pytest.raises(EnrichmentError):
            await enrich_chunk("doc", "chunk", "title", "path.md")


@pytest.mark.asyncio
async def test_enrich_chunks_propagates_enrichment_error():
    """If any chunk fails enrichment, EnrichmentError propagates from enrich_chunks."""
    chunks = ["chunk A", "chunk B"]

    async def fail_enrich(*args, **kwargs):
        raise EnrichmentError("LLM timeout")

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=fail_enrich):
        with pytest.raises(EnrichmentError):
            await enrich_chunks("doc text", chunks, "title", "path.md")


@pytest.mark.asyncio
async def test_enrich_chunks_semaphore_limits_concurrency():
    """Verify semaphore is applied (calls complete, not deadlocked)."""
    chunks = [f"chunk {i}" for i in range(10)]
    calls: list[int] = []

    async def fake_enrich(*args, **kwargs):
        calls.append(1)
        return MagicMock(context_prefix="prefix", chunk_type="conceptual", questions=["q?"])

    with patch("knowledge_ingest.enrichment.enrich_chunk", side_effect=fake_enrich):
        results = await enrich_chunks("doc", chunks, "title", "path.md")

    assert len(results) == 10
    assert len(calls) == 10


# --- SPEC-KB-021 Change 1: source-aware enrichment tests ---


@pytest.mark.asyncio
async def test_enrich_chunk_source_aware_happy_path():
    """Source fields are accepted and result contains valid content_type."""
    result_data = {
        "context_prefix": "Voys Helpdesk via webscrape (help.voys.nl): uitleg over VoIP.",
        "chunk_type": "procedural",
        "questions": ["Hoe werkt VoIP?", "Wat is een SIP trunk?"],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(result_data))
        mock_client_cls.return_value = mock_client

        result = await enrich_chunk(
            document_text="Uitleg over VoIP telefonie.",
            chunk_text="SIP trunking verbindt uw PBX met het PSTN.",
            title="VoIP Handleiding",
            path="help/voip.md",
            kb_name="Voys Helpdesk",
            connector_type="webscrape",
            source_domain="help.voys.nl",
        )

    assert result is not None
    assert result.chunk_type == "procedural"
    assert result.context_prefix == result_data["context_prefix"]
    assert len(result.questions) == 2


@pytest.mark.asyncio
async def test_enrich_chunk_chunk_type_validation():
    """Invalid chunk_type in LLM response raises EnrichmentError."""
    bad_result = {
        "context_prefix": "Some prefix.",
        "chunk_type": "something_invalid",
        "questions": ["Q1?"],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(bad_result))
        mock_client_cls.return_value = mock_client

        with pytest.raises(EnrichmentError):
            await enrich_chunk("doc", "chunk", "title", "path.md")


@pytest.mark.asyncio
async def test_enrich_chunk_llm_failure_raises():
    """LLM timeout raises EnrichmentError (no silent fallback)."""
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(EnrichmentError):
            await enrich_chunk("doc", "chunk", "title", "path.md")


@pytest.mark.asyncio
async def test_enrich_chunk_backward_compatible_no_source_fields():
    """No source fields (default empty strings) — enrich_chunk still works."""
    result_data = {
        "context_prefix": "Retourbeleid Acme.",
        "chunk_type": "reference",
        "questions": ["Wat is de retourperiode?"],
    }

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=_mock_response(result_data))
        mock_client_cls.return_value = mock_client

        # No kb_name, connector_type, source_domain provided (use defaults)
        result = await enrich_chunk(
            document_text="Retourbeleid.",
            chunk_text="30 dagen retour.",
            title="Retour",
            path="retour.md",
        )

    assert result is not None
    assert result.chunk_type == "reference"
