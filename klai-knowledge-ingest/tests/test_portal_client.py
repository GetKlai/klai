"""Tests for portal_client — taxonomy node fetching and proposal submission."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.portal_client import (
    TaxonomyProposal,
    fetch_taxonomy_nodes,
    invalidate_cache,
    submit_taxonomy_proposal,
)
from knowledge_ingest.taxonomy_classifier import TaxonomyNode


def _mock_httpx_response(status_code: int, body: object) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json = MagicMock(return_value=body)
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _mock_client(response: MagicMock) -> AsyncMock:
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=response)
    mock_client.post = AsyncMock(return_value=response)
    return mock_client


class TestFetchTaxonomyNodes:
    def setup_method(self):
        invalidate_cache("org1", "kb1")

    @pytest.mark.asyncio
    async def test_returns_nodes_when_portal_responds(self):
        nodes_data = [{"id": 1, "name": "Billing"}, {"id": 2, "name": "Technical Support"}]
        mock_resp = _mock_httpx_response(200, nodes_data)
        mock_client = _mock_client(mock_resp)

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            nodes = await fetch_taxonomy_nodes("kb1", "org1")

        assert len(nodes) == 2
        assert nodes[0].id == 1
        assert nodes[0].name == "Billing"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_token(self):
        with patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = ""
            nodes = await fetch_taxonomy_nodes("kb1", "org1")
        assert nodes == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_404(self):
        mock_resp = _mock_httpx_response(404, None)
        mock_client = _mock_client(mock_resp)

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            nodes = await fetch_taxonomy_nodes("kb1", "org1")

        assert nodes == []

    @pytest.mark.asyncio
    async def test_caches_result(self):
        nodes_data = [{"id": 1, "name": "Billing"}]
        mock_resp = _mock_httpx_response(200, nodes_data)
        mock_client = _mock_client(mock_resp)

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            nodes1 = await fetch_taxonomy_nodes("kb1", "org1")
            nodes2 = await fetch_taxonomy_nodes("kb1", "org1")  # should use cache

        # Only one HTTP call should have been made
        assert mock_client.get.call_count == 1
        assert nodes1 == nodes2

    @pytest.mark.asyncio
    async def test_returns_empty_on_portal_error(self):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            nodes = await fetch_taxonomy_nodes("kb1", "org1")

        assert nodes == []


class TestSubmitTaxonomyProposal:
    @pytest.mark.asyncio
    async def test_submits_proposal_with_token(self):
        mock_resp = _mock_httpx_response(201, {"id": 1})
        mock_client = _mock_client(mock_resp)

        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="API Documentation",
            document_count=5,
            sample_titles=["API Guide", "REST Docs"],
        )

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            await submit_taxonomy_proposal("kb1", "org1", proposal)

        mock_client.post.assert_called_once()
        call_json = mock_client.post.call_args.kwargs["json"]
        assert call_json["proposal_type"] == "new_node"
        assert call_json["suggested_name"] == "API Documentation"

    @pytest.mark.asyncio
    async def test_skips_when_no_token(self):
        mock_client = MagicMock()

        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="Something",
            document_count=3,
            sample_titles=[],
        )

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = ""
            await submit_taxonomy_proposal("kb1", "org1", proposal)

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_raise_on_portal_error(self):
        """Proposal submission errors must not propagate."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="Something",
            document_count=3,
            sample_titles=[],
        )

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            # Must not raise
            await submit_taxonomy_proposal("kb1", "org1", proposal)
