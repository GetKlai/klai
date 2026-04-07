"""Tests for _run_backfill batch proposal generation (SPEC-KB-027 R2).

All Qdrant / portal / LLM calls are mocked. The tests verify:
- maybe_generate_proposal is called with all unmatched docs after Phase 2
- maybe_generate_proposal is NOT called when all docs are matched
- proposals_submitted == 0 when no taxonomy nodes exist

Note: _run_backfill imports all dependencies inside the function body, so we patch
at their source modules (knowledge_ingest.config, knowledge_ingest.portal_client, etc.)
and use qdrant_client.AsyncQdrantClient for the Qdrant client constructor.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_qdrant_point(doc_key: str, title: str, text: str = "content") -> MagicMock:
    point = MagicMock()
    point.id = doc_key
    point.payload = {
        "artifact_id": doc_key,
        "title": title,
        "text": text,
    }
    return point


def _make_taxonomy_node(node_id: int, name: str) -> MagicMock:
    node = MagicMock()
    node.id = node_id
    node.name = name
    return node


def _make_scroll_client(phase2_points: list) -> AsyncMock:
    """Return an AsyncQdrantClient mock whose scroll() returns empty for phases 0/1/3
    and `phase2_points` for phase 2's first page."""
    call_count = 0

    async def scroll_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Phase 0 → call 1, Phase 1 → call 2, Phase 2 → call 3, Phase 3 → call 4+
        if call_count == 3:
            return (phase2_points, None)
        return ([], None)

    client = AsyncMock()
    client.scroll = scroll_side_effect
    client.set_payload = AsyncMock()
    return client


def _common_patches(client, taxonomy_nodes, classify_return):
    """Return a list of context managers for the common patches shared across tests."""
    mock_settings = MagicMock()
    mock_settings.qdrant_url = "http://qdrant:6333"
    mock_settings.qdrant_api_key = ""

    return [
        patch("qdrant_client.AsyncQdrantClient", return_value=client),
        patch("knowledge_ingest.config.settings", mock_settings),
        patch(
            "knowledge_ingest.portal_client.fetch_taxonomy_nodes",
            new=AsyncMock(return_value=taxonomy_nodes),
        ),
        patch("knowledge_ingest.portal_client.invalidate_cache"),
        patch(
            "knowledge_ingest.taxonomy_classifier.classify_document",
            new=AsyncMock(return_value=classify_return),
        ),
        patch(
            "knowledge_ingest.content_labeler.generate_content_label",
            new=AsyncMock(return_value="label"),
        ),
    ]


class TestRunBackfillProposals:
    @pytest.mark.asyncio
    async def test_maybe_generate_proposal_called_with_unmatched_docs(self):
        """When Phase 2 finds 5 unmatched docs, maybe_generate_proposal is called with all 5."""
        taxonomy_nodes = [_make_taxonomy_node(1, "Node A")]
        phase2_points = [_make_qdrant_point(f"doc{i}", f"Doc {i}") for i in range(5)]
        client = _make_scroll_client(phase2_points)

        mock_proposal = AsyncMock(return_value=True)

        patches = _common_patches(client, taxonomy_nodes, classify_return=([], []))
        patches.append(
            patch(
                "knowledge_ingest.proposal_generator.maybe_generate_proposal",
                new=mock_proposal,
            )
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            from knowledge_ingest.taxonomy_tasks import _run_backfill
            result = await _run_backfill(org_id="org1", kb_slug="kb1", batch_size=100)

        mock_proposal.assert_called_once()
        call_kwargs = mock_proposal.call_args.kwargs
        assert call_kwargs["org_id"] == "org1"
        assert call_kwargs["kb_slug"] == "kb1"
        assert len(call_kwargs["unmatched_documents"]) == 5
        assert call_kwargs["existing_nodes"] == taxonomy_nodes
        assert result["proposals_submitted"] == 1

    @pytest.mark.asyncio
    async def test_maybe_generate_proposal_not_called_when_all_matched(self):
        """When all docs in Phase 2 are matched, maybe_generate_proposal is NOT called."""
        taxonomy_nodes = [_make_taxonomy_node(1, "Node A")]
        phase2_points = [_make_qdrant_point(f"doc{i}", f"Doc {i}") for i in range(3)]
        client = _make_scroll_client(phase2_points)

        mock_proposal = AsyncMock()

        patches = _common_patches(
            client, taxonomy_nodes, classify_return=([(1, 0.9)], ["tag1"])
        )
        patches.append(
            patch(
                "knowledge_ingest.proposal_generator.maybe_generate_proposal",
                new=mock_proposal,
            )
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            from knowledge_ingest.taxonomy_tasks import _run_backfill
            result = await _run_backfill(org_id="org1", kb_slug="kb1", batch_size=100)

        mock_proposal.assert_not_called()
        assert result["proposals_submitted"] == 0

    @pytest.mark.asyncio
    async def test_proposals_submitted_zero_when_no_taxonomy_nodes(self):
        """When no taxonomy nodes exist, proposals_submitted is 0 and Phase 2 never runs."""
        client = AsyncMock()
        client.scroll = AsyncMock(return_value=([], None))
        client.set_payload = AsyncMock()

        mock_settings = MagicMock()
        mock_settings.qdrant_url = "http://qdrant:6333"
        mock_settings.qdrant_api_key = ""

        with (
            patch("qdrant_client.AsyncQdrantClient", return_value=client),
            patch("knowledge_ingest.config.settings", mock_settings),
            patch(
                "knowledge_ingest.portal_client.fetch_taxonomy_nodes",
                new=AsyncMock(return_value=[]),
            ),
            patch("knowledge_ingest.portal_client.invalidate_cache"),
            patch(
                "knowledge_ingest.content_labeler.generate_content_label",
                new=AsyncMock(return_value="label"),
            ),
        ):
            from knowledge_ingest.taxonomy_tasks import _run_backfill
            result = await _run_backfill(org_id="org1", kb_slug="kb1", batch_size=100)

        assert result["proposals_submitted"] == 0
