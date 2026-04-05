"""Tests for taxonomy_classifier — unit tests with mocked LiteLLM calls."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.taxonomy_classifier import TaxonomyNode, classify_document


def _make_nodes(*names: str) -> list[TaxonomyNode]:
    return [TaxonomyNode(id=i + 1, name=name) for i, name in enumerate(names)]


def _mock_litellm_response(node_id: int | None, confidence: float) -> AsyncMock:
    """Build a mock httpx response for the LiteLLM /chat/completions endpoint."""
    response_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "node_id": node_id,
                        "confidence": confidence,
                        "reasoning": "test",
                    })
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_json)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestClassifyDocument:
    @pytest.mark.asyncio
    async def test_returns_node_id_when_high_confidence(self):
        nodes = _make_nodes("Billing", "Technical Support")
        mock_client = _mock_litellm_response(node_id=1, confidence=0.9)
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            node_id, confidence = await classify_document("Invoice question", "...", nodes)
        assert node_id == 1
        assert confidence == 0.9

    @pytest.mark.asyncio
    async def test_returns_none_when_confidence_below_threshold(self):
        nodes = _make_nodes("Billing", "Technical Support")
        mock_client = _mock_litellm_response(node_id=1, confidence=0.3)
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            node_id, confidence = await classify_document("Ambiguous", "...", nodes)
        assert node_id is None
        assert confidence == 0.3

    @pytest.mark.asyncio
    async def test_returns_none_when_llm_returns_null_node(self):
        nodes = _make_nodes("Billing", "Technical Support")
        mock_client = _mock_litellm_response(node_id=None, confidence=0.0)
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            node_id, confidence = await classify_document("Random doc", "...", nodes)
        assert node_id is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_taxonomy_nodes(self):
        node_id, confidence = await classify_document("Any title", "any content", [])
        assert node_id is None
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        nodes = _make_nodes("Billing")
        with patch(
            "knowledge_ingest.taxonomy_classifier._call_litellm",
            side_effect=asyncio.TimeoutError(),
        ):
            node_id, confidence = await classify_document("Title", "Content", nodes)
        assert node_id is None
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_returns_none_on_http_error(self):
        nodes = _make_nodes("Billing")
        with patch(
            "knowledge_ingest.taxonomy_classifier._call_litellm",
            side_effect=Exception("connection refused"),
        ):
            node_id, confidence = await classify_document("Title", "Content", nodes)
        assert node_id is None
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_rejects_invalid_node_id_not_in_taxonomy(self):
        """LLM returns a node_id that doesn't exist — must be rejected."""
        nodes = _make_nodes("Billing")  # only id=1
        mock_client = _mock_litellm_response(node_id=999, confidence=0.95)
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            node_id, confidence = await classify_document("Title", "Content", nodes)
        assert node_id is None

    @pytest.mark.asyncio
    async def test_content_preview_truncated_to_500_chars(self):
        """Verify the classifier only sends the first 500 chars to the LLM."""
        nodes = _make_nodes("Billing")
        captured_messages = []

        async def _capture_call(user_message: str) -> dict:
            captured_messages.append(user_message)
            return {"node_id": None, "confidence": 0.0, "reasoning": "test"}

        with patch("knowledge_ingest.taxonomy_classifier._call_litellm", side_effect=_capture_call):
            long_content = "x" * 2000
            await classify_document("Title", long_content, nodes)

        assert len(captured_messages) == 1
        # The user message should contain at most 500 chars of content
        assert "x" * 501 not in captured_messages[0]
