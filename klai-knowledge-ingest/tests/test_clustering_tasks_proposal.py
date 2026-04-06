"""Tests for SPEC-KB-026 R1+R2: clustering_tasks builds TaxonomyProposal correctly.

Verifies that _generate_cluster_proposals() creates a TaxonomyProposal object
and passes it to submit_taxonomy_proposal() without TypeError.
Also verifies cluster_centroid is included in the proposal.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.portal_client import TaxonomyProposal


@dataclass
class FakeCluster:
    cluster_id: int
    centroid: list[float]
    size: int
    taxonomy_node_id: int | None
    content_label_summary: list[str]


class TestClusteringTasksProposalSubmission:
    """R1: clustering_tasks must build a TaxonomyProposal and pass it correctly."""

    @pytest.mark.asyncio
    async def test_submit_uses_taxonomy_proposal_object(self):
        """submit_taxonomy_proposal is called with (kb_slug, org_id, TaxonomyProposal)."""
        cluster = FakeCluster(
            cluster_id=0,
            centroid=[0.1, 0.2, 0.3],
            size=10,
            taxonomy_node_id=None,
            content_label_summary=["billing", "invoices"],
        )

        mock_submit = AsyncMock()
        mock_suggest = AsyncMock(return_value="Facturen")
        mock_pending = AsyncMock(return_value=[])

        with patch("knowledge_ingest.portal_client.submit_taxonomy_proposal", mock_submit), \
             patch("knowledge_ingest.clustering_tasks._suggest_category_name", mock_suggest), \
             patch("knowledge_ingest.clustering_tasks._get_pending_proposals", mock_pending):
            from knowledge_ingest.clustering_tasks import _generate_cluster_proposals

            await _generate_cluster_proposals(
                org_id="org1",
                kb_slug="kb1",
                unmatched_clusters=[cluster],
                taxonomy_nodes=[],
            )

        mock_submit.assert_called_once()
        args = mock_submit.call_args
        # Must be called as submit_taxonomy_proposal(kb_slug, org_id, proposal)
        # The first two positional-or-keyword args are kb_slug and org_id
        assert args.kwargs.get("kb_slug") == "kb1" or (args.args and args.args[0] == "kb1")
        assert args.kwargs.get("org_id") == "org1" or (len(args.args) > 1 and args.args[1] == "org1")

        # Third argument must be a TaxonomyProposal instance
        proposal = args.kwargs.get("proposal") or (args.args[2] if len(args.args) > 2 else None)
        assert isinstance(proposal, TaxonomyProposal), (
            f"Expected TaxonomyProposal, got {type(proposal)}"
        )

    @pytest.mark.asyncio
    async def test_proposal_contains_cluster_centroid(self):
        """R2: cluster_centroid must be present in the TaxonomyProposal."""
        centroid_vec = [0.1, 0.2, 0.3]
        cluster = FakeCluster(
            cluster_id=0,
            centroid=centroid_vec,
            size=10,
            taxonomy_node_id=None,
            content_label_summary=["billing"],
        )

        mock_submit = AsyncMock()
        mock_suggest = AsyncMock(return_value="Facturen")
        mock_pending = AsyncMock(return_value=[])

        with patch("knowledge_ingest.portal_client.submit_taxonomy_proposal", mock_submit), \
             patch("knowledge_ingest.clustering_tasks._suggest_category_name", mock_suggest), \
             patch("knowledge_ingest.clustering_tasks._get_pending_proposals", mock_pending):
            from knowledge_ingest.clustering_tasks import _generate_cluster_proposals

            await _generate_cluster_proposals(
                org_id="org1",
                kb_slug="kb1",
                unmatched_clusters=[cluster],
                taxonomy_nodes=[],
            )

        proposal = mock_submit.call_args.kwargs.get("proposal") or mock_submit.call_args.args[2]
        assert proposal.cluster_centroid == centroid_vec

    @pytest.mark.asyncio
    async def test_proposal_has_correct_fields(self):
        """The proposal should have proposal_type, suggested_name, document_count, sample_titles."""
        cluster = FakeCluster(
            cluster_id=0,
            centroid=[0.5, 0.6],
            size=15,
            taxonomy_node_id=None,
            content_label_summary=["hr", "onboarding", "policies"],
        )

        mock_submit = AsyncMock()
        mock_suggest = AsyncMock(return_value="HR Beleid")
        mock_pending = AsyncMock(return_value=[])

        with patch("knowledge_ingest.portal_client.submit_taxonomy_proposal", mock_submit), \
             patch("knowledge_ingest.clustering_tasks._suggest_category_name", mock_suggest), \
             patch("knowledge_ingest.clustering_tasks._get_pending_proposals", mock_pending):
            from knowledge_ingest.clustering_tasks import _generate_cluster_proposals

            await _generate_cluster_proposals(
                org_id="org1",
                kb_slug="kb1",
                unmatched_clusters=[cluster],
                taxonomy_nodes=[],
            )

        proposal = mock_submit.call_args.kwargs.get("proposal") or mock_submit.call_args.args[2]
        assert proposal.proposal_type == "new_node"
        assert proposal.suggested_name == "HR Beleid"
        assert proposal.document_count == 15


class TestTaxonomyProposalDataclass:
    """R2: TaxonomyProposal must have cluster_centroid field."""

    def test_cluster_centroid_field_exists(self):
        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="Test",
            document_count=5,
            sample_titles=["a", "b"],
            cluster_centroid=[0.1, 0.2],
        )
        assert proposal.cluster_centroid == [0.1, 0.2]

    def test_cluster_centroid_defaults_to_none(self):
        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="Test",
            document_count=5,
            sample_titles=["a"],
        )
        assert proposal.cluster_centroid is None


class TestSubmitProposalIncludesCentroid:
    """R2: submit_taxonomy_proposal payload must include cluster_centroid."""

    @pytest.mark.asyncio
    async def test_payload_includes_cluster_centroid(self):
        from knowledge_ingest.portal_client import submit_taxonomy_proposal

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="Billing",
            document_count=10,
            sample_titles=["Invoice Guide"],
            cluster_centroid=[0.1, 0.2, 0.3],
        )

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            await submit_taxonomy_proposal("kb1", "org1", proposal)

        call_json = mock_client.post.call_args.kwargs["json"]
        assert call_json["payload"]["cluster_centroid"] == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_payload_centroid_null_when_none(self):
        from knowledge_ingest.portal_client import submit_taxonomy_proposal

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name="General",
            document_count=5,
            sample_titles=["Doc1"],
        )

        with patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client), \
             patch("knowledge_ingest.portal_client.settings") as mock_settings:
            mock_settings.portal_internal_token = "secret"
            mock_settings.portal_url = "http://portal-api:8000"
            await submit_taxonomy_proposal("kb1", "org1", proposal)

        call_json = mock_client.post.call_args.kwargs["json"]
        assert call_json["payload"]["cluster_centroid"] is None
