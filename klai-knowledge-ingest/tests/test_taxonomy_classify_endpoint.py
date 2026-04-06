"""Tests for SPEC-KB-026 R4 part 1: POST /ingest/v1/taxonomy/classify endpoint.

Verifies the new classify endpoint calls classify_document and returns node IDs.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestTaxonomyClassifyEndpoint:
    """R4/1: New classify endpoint in knowledge-ingest."""

    @pytest.mark.asyncio
    async def test_classify_returns_node_ids(self, client):
        """Successful classification returns taxonomy_node_ids."""
        mock_nodes = [
            type("TaxonomyNode", (), {"id": 5, "name": "Billing", "description": None})(),
            type("TaxonomyNode", (), {"id": 7, "name": "Technical", "description": None})(),
        ]
        mock_classify = AsyncMock(return_value=([(5, 0.9), (7, 0.7)], ["billing"]))
        mock_fetch = AsyncMock(return_value=mock_nodes)

        with patch("knowledge_ingest.routes.taxonomy.classify_document", mock_classify), \
             patch("knowledge_ingest.routes.taxonomy.fetch_taxonomy_nodes", mock_fetch):
            resp = client.post(
                "/ingest/v1/taxonomy/classify",
                json={"org_id": "org1", "kb_slug": "kb1", "text": "How do I pay my invoice?"},
                headers={"X-Internal-Secret": ""},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["taxonomy_node_ids"] == [5, 7]

    @pytest.mark.asyncio
    async def test_classify_returns_empty_when_no_nodes(self, client):
        """When no taxonomy nodes exist, return empty list."""
        mock_fetch = AsyncMock(return_value=[])

        with patch("knowledge_ingest.routes.taxonomy.fetch_taxonomy_nodes", mock_fetch):
            resp = client.post(
                "/ingest/v1/taxonomy/classify",
                json={"org_id": "org1", "kb_slug": "kb1", "text": "Some query"},
                headers={"X-Internal-Secret": ""},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["taxonomy_node_ids"] == []

    @pytest.mark.asyncio
    async def test_classify_returns_empty_on_no_match(self, client):
        """When classify_document returns no matches, return empty list."""
        mock_nodes = [
            type("TaxonomyNode", (), {"id": 1, "name": "Finance", "description": None})(),
        ]
        mock_classify = AsyncMock(return_value=([], []))
        mock_fetch = AsyncMock(return_value=mock_nodes)

        with patch("knowledge_ingest.routes.taxonomy.classify_document", mock_classify), \
             patch("knowledge_ingest.routes.taxonomy.fetch_taxonomy_nodes", mock_fetch):
            resp = client.post(
                "/ingest/v1/taxonomy/classify",
                json={"org_id": "org1", "kb_slug": "kb1", "text": "unrelated query"},
                headers={"X-Internal-Secret": ""},
            )

        assert resp.status_code == 200
        assert resp.json()["taxonomy_node_ids"] == []
