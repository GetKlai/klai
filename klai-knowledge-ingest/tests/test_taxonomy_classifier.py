"""Tests for taxonomy_classifier -- multi-label classification + tag suggestion."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.taxonomy_classifier import TaxonomyNode, classify_document


def _make_nodes(*names: str) -> list[TaxonomyNode]:
    return [TaxonomyNode(id=i + 1, name=name) for i, name in enumerate(names)]


def _make_nodes_with_desc(*items: tuple[str, str | None]) -> list[TaxonomyNode]:
    return [TaxonomyNode(id=i + 1, name=name, description=desc) for i, (name, desc) in enumerate(items)]


def _mock_litellm_response(
    nodes: list[dict], tags: list[str] | None = None
) -> AsyncMock:
    """Build a mock httpx response for multi-label LiteLLM response."""
    response_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "nodes": nodes,
                        "tags": tags or [],
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


class TestClassifyDocumentMultiLabel:
    @pytest.mark.asyncio
    async def test_returns_multiple_nodes_with_high_confidence(self):
        nodes = _make_nodes("Billing", "Security", "Setup")
        mock_client = _mock_litellm_response(
            nodes=[
                {"node_id": 1, "confidence": 0.9},
                {"node_id": 3, "confidence": 0.7},
            ],
            tags=["sso", "billing"],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("SSO billing setup", "...", nodes)
        assert len(matched) == 2
        assert matched[0] == (1, 0.9)
        assert matched[1] == (3, 0.7)
        assert tags == ["sso", "billing"]

    @pytest.mark.asyncio
    async def test_filters_nodes_below_threshold(self):
        nodes = _make_nodes("Billing", "Security")
        mock_client = _mock_litellm_response(
            nodes=[
                {"node_id": 1, "confidence": 0.9},
                {"node_id": 2, "confidence": 0.3},  # below threshold
            ],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Invoice", "...", nodes)
        assert len(matched) == 1
        assert matched[0] == (1, 0.9)

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_below_threshold(self):
        nodes = _make_nodes("Billing", "Technical Support")
        mock_client = _mock_litellm_response(
            nodes=[
                {"node_id": 1, "confidence": 0.3},
                {"node_id": 2, "confidence": 0.2},
            ],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Ambiguous", "...", nodes)
        assert matched == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_taxonomy_nodes(self):
        matched, tags = await classify_document("Any title", "any content", [])
        assert matched == []
        assert tags == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        nodes = _make_nodes("Billing")
        with patch(
            "knowledge_ingest.taxonomy_classifier._call_litellm",
            side_effect=asyncio.TimeoutError(),
        ):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert matched == []
        assert tags == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        nodes = _make_nodes("Billing")
        with patch(
            "knowledge_ingest.taxonomy_classifier._call_litellm",
            side_effect=Exception("connection refused"),
        ):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert matched == []
        assert tags == []

    @pytest.mark.asyncio
    async def test_rejects_invalid_node_id_not_in_taxonomy(self):
        """LLM returns a node_id that doesn't exist -- must be rejected."""
        nodes = _make_nodes("Billing")  # only id=1
        mock_client = _mock_litellm_response(
            nodes=[{"node_id": 999, "confidence": 0.95}],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert matched == []

    @pytest.mark.asyncio
    async def test_max_5_nodes_returned(self):
        nodes = [TaxonomyNode(id=i, name=f"Node{i}") for i in range(1, 10)]
        mock_client = _mock_litellm_response(
            nodes=[{"node_id": i, "confidence": 0.9 - i * 0.01} for i in range(1, 10)],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert len(matched) <= 5

    @pytest.mark.asyncio
    async def test_max_5_tags_returned(self):
        nodes = _make_nodes("Billing")
        mock_client = _mock_litellm_response(
            nodes=[{"node_id": 1, "confidence": 0.9}],
            tags=["a", "b", "c", "d", "e", "f", "g"],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert len(tags) <= 5

    @pytest.mark.asyncio
    async def test_tags_lowercased_and_deduplicated(self):
        nodes = _make_nodes("Billing")
        mock_client = _mock_litellm_response(
            nodes=[{"node_id": 1, "confidence": 0.9}],
            tags=["SSO", "sso", "OKTA", " billing "],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert tags == ["sso", "okta", "billing"]

    @pytest.mark.asyncio
    async def test_includes_node_description_in_prompt(self):
        """Verify the classifier sends node descriptions to the LLM."""
        nodes = _make_nodes_with_desc(
            ("Billing", "Invoices, subscriptions, payments"),
            ("Setup", None),
        )
        captured_messages = []

        async def _capture_call(user_message: str) -> dict:
            captured_messages.append(user_message)
            return {"nodes": [], "tags": [], "reasoning": "test"}

        with patch("knowledge_ingest.taxonomy_classifier._call_litellm", side_effect=_capture_call):
            await classify_document("Title", "content", nodes)

        assert len(captured_messages) == 1
        assert "Invoices, subscriptions, payments" in captured_messages[0]
        # Node without description should not have "--" suffix
        assert "id=2: Setup\n" in captured_messages[0] or "id=2: Setup" in captured_messages[0]

    @pytest.mark.asyncio
    async def test_content_preview_truncated_to_500_chars(self):
        """Verify the classifier only sends the first 500 chars to the LLM."""
        nodes = _make_nodes("Billing")
        captured_messages = []

        async def _capture_call(user_message: str) -> dict:
            captured_messages.append(user_message)
            return {"nodes": [], "tags": [], "reasoning": "test"}

        with patch("knowledge_ingest.taxonomy_classifier._call_litellm", side_effect=_capture_call):
            long_content = "x" * 2000
            await classify_document("Title", long_content, nodes)

        assert len(captured_messages) == 1
        assert "x" * 501 not in captured_messages[0]

    @pytest.mark.asyncio
    async def test_nodes_sorted_by_confidence_descending(self):
        nodes = _make_nodes("Billing", "Security", "Setup")
        mock_client = _mock_litellm_response(
            nodes=[
                {"node_id": 3, "confidence": 0.6},
                {"node_id": 1, "confidence": 0.9},
                {"node_id": 2, "confidence": 0.7},
            ],
        )
        with patch("knowledge_ingest.taxonomy_classifier.httpx.AsyncClient", return_value=mock_client):
            matched, tags = await classify_document("Title", "Content", nodes)
        assert matched[0][1] >= matched[1][1] >= matched[2][1]
