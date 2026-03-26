"""Tests for knowledge_ingest.graph module."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import graph as graph_module


def _make_episode_result(uuid: str = "ep-001") -> MagicMock:
    result = MagicMock()
    result.uuid = uuid
    result.entity_count = 3
    result.edge_count = 2
    return result


@pytest.mark.asyncio
async def test_ingest_episode_disabled():
    """Returns None immediately when GRAPHITI_ENABLED=false (AC-8)."""
    with patch("knowledge_ingest.graph.settings") as mock_settings:
        mock_settings.graphiti_enabled = False
        result = await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="Hello world",
            org_id="org-1",
            content_type="markdown",
            belief_time_start=1700000000,
        )
    assert result is None


@pytest.mark.asyncio
async def test_ingest_episode_success():
    """Returns episode_id on success (AC-1, AC-2, AC-13)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.add_episode = AsyncMock(return_value=_make_episode_result("ep-abc"))

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        result = await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="Hello world",
            org_id="org-1",
            content_type="markdown",
            belief_time_start=1700000000,
        )

    assert result == "ep-abc"
    mock_graphiti.add_episode.assert_called_once()
    call_kwargs = mock_graphiti.add_episode.call_args.kwargs
    assert call_kwargs["group_id"] == "org-1"
    assert call_kwargs["name"] == "art-1"


@pytest.mark.asyncio
async def test_ingest_episode_retry_success():
    """Retries on failure and returns episode_id after second attempt (AC-3)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.add_episode = AsyncMock(
        side_effect=[Exception("timeout"), _make_episode_result("ep-retry")]
    )

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=mock_graphiti),
        patch("knowledge_ingest.graph.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_settings.graphiti_enabled = True
        result = await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="Hello",
            org_id="org-1",
            content_type="text",
            belief_time_start=1700000000,
        )

    assert result == "ep-retry"
    assert mock_graphiti.add_episode.call_count == 2


@pytest.mark.asyncio
async def test_ingest_episode_all_retries_fail():
    """Returns None after all 3 retries fail — document still searchable via Qdrant (AC-3)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.add_episode = AsyncMock(side_effect=Exception("falkordb down"))

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=mock_graphiti),
        patch("knowledge_ingest.graph.asyncio.sleep", new_callable=AsyncMock),
    ):
        mock_settings.graphiti_enabled = True
        result = await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="Hello",
            org_id="org-1",
            content_type="text",
            belief_time_start=1700000000,
        )

    assert result is None
    assert mock_graphiti.add_episode.call_count == 3


@pytest.mark.asyncio
async def test_ingest_episode_reference_time_matches_belief_time_start():
    """reference_time is derived from belief_time_start (AC-1)."""
    from datetime import datetime, timezone

    mock_graphiti = AsyncMock()
    mock_graphiti.add_episode = AsyncMock(return_value=_make_episode_result("ep-time"))

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="Hello",
            org_id="org-1",
            content_type="text",
            belief_time_start=1700000000,
        )

    call_kwargs = mock_graphiti.add_episode.call_args.kwargs
    expected_dt = datetime.fromtimestamp(1700000000, tz=timezone.utc)
    assert call_kwargs["reference_time"] == expected_dt
