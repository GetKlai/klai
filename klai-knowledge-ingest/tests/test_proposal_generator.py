"""Tests for proposal_generator — batch unmatched document proposal logic."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.proposal_generator import DocumentSummary, maybe_generate_proposal
from knowledge_ingest.taxonomy_classifier import TaxonomyNode


def _make_docs(count: int) -> list[DocumentSummary]:
    return [DocumentSummary(title=f"Doc {i}", content_preview=f"Content {i}") for i in range(count)]


def _make_nodes(*names: str) -> list[TaxonomyNode]:
    return [TaxonomyNode(id=i + 1, name=name) for i, name in enumerate(names)]


def _mock_litellm_category(name: str) -> AsyncMock:
    response_json = {
        "choices": [{"message": {"content": json.dumps({"category_name": name})}}]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_json)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestMaybeGenerateProposal:
    @pytest.mark.asyncio
    async def test_no_proposal_below_threshold(self):
        """Less than 3 unmatched documents → no proposal submitted."""
        docs = _make_docs(2)
        nodes = _make_nodes("Billing")

        with patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal") as mock_submit:
            with patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
                mock_settings.portal_internal_token = "secret"
                mock_settings.taxonomy_classification_timeout = 5.0
                mock_settings.litellm_url = "http://litellm:4000"
                mock_settings.litellm_api_key = "key"
                mock_settings.taxonomy_classification_model = "klai-fast"
                await maybe_generate_proposal("org1", "kb1", docs, nodes)
            mock_submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_proposal_when_no_token(self):
        """Missing PORTAL_INTERNAL_TOKEN → skips silently."""
        docs = _make_docs(5)
        nodes = _make_nodes("Billing")

        with patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal") as mock_submit:
            with patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
                mock_settings.portal_internal_token = ""
                await maybe_generate_proposal("org1", "kb1", docs, nodes)
            mock_submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_proposal_when_name_already_exists(self):
        """Suggested name matches existing node → no duplicate submitted."""
        docs = _make_docs(5)
        nodes = _make_nodes("API Documentation", "Billing")

        mock_client = _mock_litellm_category("API Documentation")

        with patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal") as mock_submit, \
             patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 5.0
            mock_settings.litellm_url = "http://litellm:4000"
            mock_settings.litellm_api_key = "key"
            mock_settings.taxonomy_classification_model = "klai-fast"
            await maybe_generate_proposal("org1", "kb1", docs, nodes)

        mock_submit.assert_not_called()

    @pytest.mark.asyncio
    async def test_submits_proposal_when_threshold_met(self):
        """3+ unmatched docs + new name → proposal submitted."""
        docs = _make_docs(4)
        nodes = _make_nodes("Billing")

        mock_client = _mock_litellm_category("Developer Resources")

        with patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", new_callable=AsyncMock) as mock_submit, \
             patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 5.0
            mock_settings.litellm_url = "http://litellm:4000"
            mock_settings.litellm_api_key = "key"
            mock_settings.taxonomy_classification_model = "klai-fast"
            await maybe_generate_proposal("org1", "kb1", docs, nodes)

        mock_submit.assert_called_once()
        proposal = mock_submit.call_args.kwargs["proposal"]
        assert proposal.suggested_name == "Developer Resources"
        assert proposal.proposal_type == "new_node"
        assert proposal.document_count == 4
