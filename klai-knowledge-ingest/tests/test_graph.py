"""Tests for knowledge_ingest.graph module."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import graph as graph_module


class _FakeEpisodeType:
    text = "text"


@pytest.fixture(autouse=True)
def _patch_episode_type(monkeypatch):
    """Inject EpisodeType into graph module so tests run without graphiti-core installed."""
    monkeypatch.setattr(graph_module, "EpisodeType", _FakeEpisodeType, raising=False)


@pytest.fixture(autouse=True)
def _reset_semaphore():
    """Reset the cached semaphore between tests."""
    graph_module._episode_semaphore = None
    yield
    graph_module._episode_semaphore = None


def _make_episode_result(uuid: str = "ep-001") -> MagicMock:
    episode_node = MagicMock()
    episode_node.uuid = uuid
    result = MagicMock()
    result.episode = episode_node
    result.nodes = []
    result.edges = []
    return result


@pytest.mark.asyncio
async def test_document_entity_graph_data_accumulates_entities_across_episode_parts():
    graphiti = AsyncMock()
    first = _make_episode_result("ep-1")
    first.nodes = [SimpleNamespace(uuid="entity-1", name="Alpha")]
    second = _make_episode_result("ep-2")
    second.nodes = [SimpleNamespace(uuid="entity-2", name="Bravo")]
    graphiti.add_episode = AsyncMock(side_effect=[first, second])
    entity_data = graph_module.EntityGraphData()

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=graphiti),
        patch(
            "knowledge_ingest.graph.compute_entity_pagerank",
            new=AsyncMock(return_value={"entity-1": 0.2, "entity-2": 0.7}),
        ),
        patch(
            "knowledge_ingest.graph.qdrant_store.set_entity_graph_data",
            new=AsyncMock(return_value=None),
        ) as set_entity_graph_data,
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
        for body in ("Part one", "Part two"):
            await graph_module.ingest_episode(
                artifact_id="art-1",
                document_text=body,
                org_id="org-1",
                content_type="markdown",
                belief_time_start=1700000000,
                entity_graph_data=entity_data,
            )
        await graph_module.flush_entity_graph_data("art-1", "org-1", entity_data)

    assert set_entity_graph_data.await_args.kwargs["entity_uuids"] == [
        "entity-1",
        "entity-2",
    ]
    assert set_entity_graph_data.await_args.kwargs["entity_names"] == ["Alpha", "Bravo"]


@pytest.mark.asyncio
async def test_document_entity_graph_data_computes_pagerank_once_after_all_parts():
    graphiti = AsyncMock()
    first = _make_episode_result("ep-1")
    first.nodes = [SimpleNamespace(uuid="entity-1", name="Alpha")]
    second = _make_episode_result("ep-2")
    second.nodes = [SimpleNamespace(uuid="entity-2", name="Bravo")]
    graphiti.add_episode = AsyncMock(side_effect=[first, second])
    entity_data = graph_module.EntityGraphData()

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=graphiti),
        patch(
            "knowledge_ingest.graph.compute_entity_pagerank",
            new=AsyncMock(return_value={}),
        ) as compute_entity_pagerank,
        patch(
            "knowledge_ingest.graph.qdrant_store.set_entity_graph_data",
            new=AsyncMock(return_value=None),
        ),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
        for body in ("Part one", "Part two"):
            await graph_module.ingest_episode(
                artifact_id="art-1",
                document_text=body,
                org_id="org-1",
                content_type="markdown",
                belief_time_start=1700000000,
                entity_graph_data=entity_data,
            )
        await graph_module.flush_entity_graph_data("art-1", "org-1", entity_data)

    compute_entity_pagerank.assert_awaited_once_with("org-1")


def test_graphiti_retry_delay_rate_limit_waits_for_minute_window():
    reason, wait = graph_module._graphiti_retry_delay(Exception("Mistral 429 rate limit"), 0)

    assert reason == "rate_limited"
    assert wait == 65.0


def test_graphiti_retry_delay_provider_unavailable_uses_long_backoff():
    reason, wait = graph_module._graphiti_retry_delay(
        Exception('MistralException - {"code":"3800","raw_status_code":503}'),
        1,
    )

    assert reason == "provider_unavailable"
    assert wait == 60.0


def test_graphiti_retry_delay_other_errors_stay_short():
    reason, wait = graph_module._graphiti_retry_delay(Exception("validation failed"), 1)

    assert reason == "transient"
    assert wait == 2.0


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
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
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
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
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
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
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
    mock_graphiti = AsyncMock()
    mock_graphiti.add_episode = AsyncMock(return_value=_make_episode_result("ep-time"))

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
        await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="Hello",
            org_id="org-1",
            content_type="text",
            belief_time_start=1700000000,
        )

    call_kwargs = mock_graphiti.add_episode.call_args.kwargs
    expected_dt = datetime.fromtimestamp(1700000000, tz=UTC)
    assert call_kwargs["reference_time"] == expected_dt


# ---------------------------------------------------------------------------
# Extraction-instruction contract -- GetKlai/klai#1148
# ---------------------------------------------------------------------------


def test_extraction_instructions_ban_document_meta():
    """The prompt rule that #1148 exists for must be present, not just wired.

    Measured on live Voys retrievals on 2026-08-21: edge facts whose subject
    was the document ("De paginamap identificeert...").

    Language used to be rule 2 here. It now lives in ``_LANGUAGE_POLICY``,
    which reaches every graphiti LLM call rather than only the prompts this
    string is interpolated into — see tests/test_graph_language_policy.py.
    """
    instructions = graph_module._EXTRACTION_INSTRUCTIONS

    lowered = instructions.lower()
    assert "never about the document" in lowered
    assert "table of contents" in lowered
    # The observed production string is kept as the worked example.
    assert "De paginamap identificeert de Voys-app" in instructions
    assert "documentatieartikelen" in lowered and "getiteld" in lowered
    assert "handleiding 'wachtrijstatistieken'" in lowered and "beschrijft" in lowered
    assert "onderwerp van de handleiding 'statistieken'" in lowered
    assert "vallen onder de apparatuursectie" in lowered
    # Language must NOT be restated here; two copies drift.
    assert "language of the source text" not in lowered
    assert "_LANGUAGE_POLICY" in instructions


@pytest.mark.asyncio
async def test_ingest_episode_passes_extraction_instructions():
    """Every episode carries the #1148 rules into Graphiti's prompts."""
    mock_graphiti = AsyncMock()
    mock_graphiti.add_episode = AsyncMock(return_value=_make_episode_result("ep-abc"))

    with (
        patch("knowledge_ingest.graph.settings") as mock_settings,
        patch("knowledge_ingest.graph._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graphiti_max_concurrent = 1
        mock_settings.graphiti_episode_delay = 0
        await graph_module.ingest_episode(
            artifact_id="art-1",
            document_text="De Voys-app gebruikt de internetverbinding.",
            org_id="org-1",
            content_type="markdown",
            belief_time_start=1700000000,
        )

    call_kwargs = mock_graphiti.add_episode.call_args.kwargs
    assert call_kwargs["custom_extraction_instructions"] is graph_module._EXTRACTION_INSTRUCTIONS


def test_graphiti_still_accepts_custom_extraction_instructions():
    """``add_episode`` must still take the kwarg we steer extraction with.

    Without this, a graphiti-core bump that renames or drops the hook would
    leave ingest_episode() passing an argument nothing reads, and the graph
    would silently go back to producing the #1148 meta-facts. Same failure
    mode as GetKlai/klai#1137, where a mocked SDK hid a removed method.
    """
    import inspect

    pytest.importorskip("graphiti_core")
    from graphiti_core import Graphiti

    params = inspect.signature(Graphiti.add_episode).parameters
    assert "custom_extraction_instructions" in params, (
        "graphiti-core no longer accepts custom_extraction_instructions — "
        "knowledge_ingest/graph.py relies on it (GetKlai/klai#1148)"
    )


def test_graphiti_injects_extraction_instructions_into_both_prompts():
    """The hook must reach entity AND edge extraction.

    #1148 is a complaint about edge ``fact`` text, so a hook that only lands
    in the entity prompt would not fix it.
    """
    import inspect

    pytest.importorskip("graphiti_core")
    from graphiti_core.prompts import extract_edges, extract_nodes

    edge_src = inspect.getsource(extract_edges)
    node_src = inspect.getsource(extract_nodes)
    assert "custom_extraction_instructions" in edge_src
    assert "custom_extraction_instructions" in node_src
