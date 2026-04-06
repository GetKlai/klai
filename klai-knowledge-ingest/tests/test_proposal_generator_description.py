"""Tests for SPEC-KB-026 R3: maybe_generate_proposal calls generate_node_description.

Verifies that incremental proposals (not bootstrap) include a generated description.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest.portal_client import TaxonomyProposal
from knowledge_ingest.proposal_generator import DocumentSummary, maybe_generate_proposal
from knowledge_ingest.taxonomy_classifier import TaxonomyNode


class TestMaybeGenerateProposalDescription:
    """R3: maybe_generate_proposal must call generate_node_description."""

    @pytest.mark.asyncio
    async def test_calls_generate_node_description(self):
        """generate_node_description must be called with the suggested_name."""
        docs = [
            DocumentSummary(title=f"Doc {i}", content_preview=f"Content {i}")
            for i in range(5)
        ]
        existing_nodes: list[TaxonomyNode] = []

        mock_suggest = AsyncMock(return_value="Facturen")
        mock_desc = AsyncMock(return_value="Categorie voor factuurgerelateerde documenten")
        mock_submit = AsyncMock()

        with patch("knowledge_ingest.proposal_generator._suggest_category_name", mock_suggest), \
             patch("knowledge_ingest.proposal_generator.generate_node_description", mock_desc), \
             patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", mock_submit), \
             patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 30.0

            await maybe_generate_proposal(
                org_id="org1",
                kb_slug="kb1",
                unmatched_documents=docs,
                existing_nodes=existing_nodes,
            )

        mock_desc.assert_called_once()
        # First arg should be the suggested_name
        assert mock_desc.call_args.args[0] == "Facturen"

    @pytest.mark.asyncio
    async def test_description_included_in_proposal(self):
        """The generated description must end up in the TaxonomyProposal."""
        docs = [
            DocumentSummary(title=f"Doc {i}", content_preview=f"Content {i}")
            for i in range(5)
        ]

        mock_suggest = AsyncMock(return_value="HR Beleid")
        mock_desc = AsyncMock(return_value="Human resources policy documents")
        mock_submit = AsyncMock()

        with patch("knowledge_ingest.proposal_generator._suggest_category_name", mock_suggest), \
             patch("knowledge_ingest.proposal_generator.generate_node_description", mock_desc), \
             patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", mock_submit), \
             patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 30.0

            await maybe_generate_proposal(
                org_id="org1",
                kb_slug="kb1",
                unmatched_documents=docs,
                existing_nodes=[],
            )

        proposal = mock_submit.call_args.kwargs.get("proposal") or mock_submit.call_args.args[2]
        assert isinstance(proposal, TaxonomyProposal)
        assert proposal.description == "Human resources policy documents"

    @pytest.mark.asyncio
    async def test_description_fallback_on_error(self):
        """If generate_node_description raises, description falls back to empty string."""
        docs = [
            DocumentSummary(title=f"Doc {i}", content_preview=f"Content {i}")
            for i in range(5)
        ]

        mock_suggest = AsyncMock(return_value="Technisch")
        mock_desc = AsyncMock(side_effect=Exception("LLM timeout"))
        mock_submit = AsyncMock()

        with patch("knowledge_ingest.proposal_generator._suggest_category_name", mock_suggest), \
             patch("knowledge_ingest.proposal_generator.generate_node_description", mock_desc), \
             patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", mock_submit), \
             patch("knowledge_ingest.proposal_generator.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.taxonomy_classification_timeout = 30.0

            await maybe_generate_proposal(
                org_id="org1",
                kb_slug="kb1",
                unmatched_documents=docs,
                existing_nodes=[],
            )

        proposal = mock_submit.call_args.kwargs.get("proposal") or mock_submit.call_args.args[2]
        assert proposal.description == ""
