"""Tests for retrieval_api.services.graph_search and RRF merge."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval_api.api.retrieve import _rrf_merge
from retrieval_api.services import graph_search
from retrieval_api.services.evidence_pack import build_evidence_pack, chunk_source_key


def _make_graph_result(
    uuid: str,
    fact: str,
    score: float = 0.8,
    weight: float | None = None,
    episodes: list[str] | None = None,
    valid_at: object | None = None,
    invalid_at: object | None = None,
) -> MagicMock:
    """Build a fake Graphiti EdgeResult for tests.

    ``weight`` MUST be set explicitly because ``_convert_results`` in
    ``graph_search.py`` reads ``getattr(r, "weight", None)`` and applies a
    Hebbian boost when it is non-None and > 0. A bare ``MagicMock`` returns
    a fresh MagicMock for every attribute access — including ``r.weight``.
    ``float(MagicMock())`` evaluates to ``1.0`` via the default
    ``__float__`` magic, so the boost would silently fire and the test
    would assert against a boosted score instead of the base ``score``.
    Defaulting to ``None`` matches the production "no Hebbian data" path.
    """
    r = MagicMock()
    r.uuid = uuid
    r.fact = fact
    r.score = score
    r.weight = weight
    # Set explicitly for the same reason ``weight`` is: a bare MagicMock
    # attribute is truthy and iterable-ish, so leaving these unset would let a
    # test pass against accidental provenance instead of the real path.
    r.episodes = episodes if episodes is not None else []
    r.valid_at = valid_at
    r.invalid_at = invalid_at
    return r


@pytest.mark.asyncio
async def test_search_disabled():
    """Returns empty list immediately when GRAPHITI_ENABLED=false (AC-8)."""
    with patch("retrieval_api.services.graph_search.settings") as mock_settings:
        mock_settings.graphiti_enabled = False
        result = await graph_search.search("query", "org-1")
    assert result == []


@pytest.mark.asyncio
async def test_search_success():
    """Returns converted chunk-compatible dicts on success."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(return_value=[_make_graph_result("e1", "Mark decided X", 0.9)])

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1", top_k=10)

    assert len(result) == 1
    assert result[0]["chunk_id"] == "graph:e1"
    assert result[0]["text"] == "Mark decided X"
    assert result[0]["score"] == 0.9
    assert result[0]["content_type"] == "graph_edge"
    mock_graphiti.search.assert_called_once()
    assert mock_graphiti.search.call_args.kwargs.get("group_ids") == ["org-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["q", "中", "café"])
async def test_search_allows_unicode_alnum_queries(query):
    """Valid one-token and non-ASCII queries must still reach Graphiti."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(return_value=[])

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search(query, "org-1", top_k=10)

    assert result == []
    mock_graphiti.search.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "?!()"])
async def test_search_skips_queries_without_searchable_text(query):
    """Graphiti can turn empty text into invalid FalkorDB full-text syntax."""
    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti") as mock_get_graphiti,
    ):
        mock_settings.graphiti_enabled = True
        result = await graph_search.search(query, "org-1")

    assert result == []
    mock_get_graphiti.assert_not_called()


@pytest.mark.asyncio
async def test_search_skips_graphiti_empty_fulltext_syntax_error():
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(
        side_effect=RuntimeError('RediSearch syntax error near "(@group_id:\\"org-1\\") ()"')
    )

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
        patch("retrieval_api.services.graph_search.logger") as mock_logger,
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("①②", "org-1")

    assert result == []
    mock_logger.info.assert_called_once_with("graph_search_skipped_empty_query", org_id="org-1")
    mock_logger.warning.assert_not_called()


@pytest.mark.asyncio
async def test_search_warns_on_non_empty_fulltext_syntax_error():
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(
        side_effect=RuntimeError('RediSearch syntax error near "(@group_id:\\"org-1\\") (hello)"')
    )

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
        patch("retrieval_api.services.graph_search.logger") as mock_logger,
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("hello", "org-1")

    assert result == []
    mock_logger.info.assert_not_called()
    mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_search_timeout():
    """Returns empty list on timeout — graceful degradation (AC-7)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(side_effect=asyncio.TimeoutError)

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1")

    assert result == []


@pytest.mark.asyncio
async def test_search_applies_hebbian_boost_when_weight_set():
    """When the EdgeResult carries a positive ``weight`` (Hebbian
    reinforcement count), the score is multiplied by ``1 + 0.1 * log1p(weight)``.

    Locks in the boost contract that ``_make_graph_result`` defaults to no
    boost for; without this test a regression that drops the weight-aware
    branch from ``_convert_results`` would only surface in production.
    """
    import math

    base_score = 0.5
    weight = 10.0
    expected = base_score * (1.0 + 0.1 * math.log1p(weight))

    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(
        return_value=[_make_graph_result("e1", "Mark decided X", base_score, weight=weight)]
    )

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1", top_k=10)

    assert len(result) == 1
    assert result[0]["score"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_search_exception():
    """Returns empty list on generic exception — graceful degradation (AC-7)."""
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(side_effect=RuntimeError("connection refused"))

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1")

    assert result == []


def test_rrf_merge_combines_results():
    """RRF merge produces combined result set with updated scores (AC-5)."""
    qdrant = [
        {
            "chunk_id": "q1",
            "text": "a",
            "score": 0.9,
            "artifact_id": None,
            "content_type": None,
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
        },
        {
            "chunk_id": "q2",
            "text": "b",
            "score": 0.8,
            "artifact_id": None,
            "content_type": None,
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
        },
    ]
    graph = [
        {
            "chunk_id": "graph:g1",
            "text": "c",
            "score": 0.7,
            "artifact_id": None,
            "content_type": "graph_edge",
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
        },
    ]
    merged = _rrf_merge(qdrant, graph)

    assert len(merged) == 3
    chunk_ids = [r["chunk_id"] for r in merged]
    assert "q1" in chunk_ids
    assert "q2" in chunk_ids
    assert "graph:g1" in chunk_ids
    # q1 should rank highest (top of both Qdrant rank)
    assert merged[0]["chunk_id"] == "q1"


def test_rrf_merge_empty_graph():
    """RRF with empty graph results preserves Qdrant order (AC-5)."""
    qdrant = [
        {
            "chunk_id": "q1",
            "text": "a",
            "score": 0.9,
            "artifact_id": None,
            "content_type": None,
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
        },
        {
            "chunk_id": "q2",
            "text": "b",
            "score": 0.8,
            "artifact_id": None,
            "content_type": None,
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
        },
    ]
    merged = _rrf_merge(qdrant, [])

    assert len(merged) == 2
    assert merged[0]["chunk_id"] == "q1"
    assert merged[1]["chunk_id"] == "q2"


def test_rrf_merge_deduplication():
    """Chunk appearing in both lists is deduplicated in output."""
    shared = {
        "chunk_id": "shared",
        "text": "x",
        "score": 0.5,
        "artifact_id": None,
        "content_type": None,
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
    }
    qdrant = [dict(shared)]
    graph = [dict(shared)]
    merged = _rrf_merge(qdrant, graph)

    chunk_ids = [r["chunk_id"] for r in merged]
    assert chunk_ids.count("shared") == 1


def test_rrf_merge_fused_scores_pin_denominator():
    """Pin the exact RRF denominator ``1 / (k + rank + 1)`` with k=60.

    The ordering-only assertions above survive a ``k + rank + 1 -> k + rank``
    mutation because both forms are monotonic in rank, so the sort order is
    unchanged and only the absolute fused scores differ. This pins those scores
    so the constant cannot silently drift.
    """
    base = {
        "text": "x",
        "score": 0.5,
        "artifact_id": None,
        "content_type": None,
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
    }
    qdrant = [{**base, "chunk_id": "shared"}, {**base, "chunk_id": "q_only"}]
    graph = [{**base, "chunk_id": "shared"}]
    merged = _rrf_merge(qdrant, graph)

    scores = {r["chunk_id"]: r["score"] for r in merged}
    # shared: rank 0 in qdrant AND rank 0 in graph -> 1/61 + 1/61
    assert scores["shared"] == pytest.approx(2.0 / 61.0)
    # q_only: rank 1 in qdrant only -> 1/62
    assert scores["q_only"] == pytest.approx(1.0 / 62.0)


@pytest.mark.asyncio
async def test_close_closes_and_clears_graphiti_client(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(graph_search, "_graphiti_client", client)

    await graph_search.close()

    client.close.assert_awaited_once_with()
    assert graph_search._graphiti_client is None


@pytest.mark.asyncio
async def test_close_is_safe_before_client_initialization(monkeypatch):
    monkeypatch.setattr(graph_search, "_graphiti_client", None)

    await graph_search.close()

    assert graph_search._graphiti_client is None


# ---------------------------------------------------------------------------
# Provenance: graph edges must be citable (SPEC-RAG-GRAPH-CITE-001)
# ---------------------------------------------------------------------------


def _make_episode(uuid: str, name: str, group_id: str) -> MagicMock:
    """Stand-in for a Graphiti EpisodicNode.

    ``name`` is the Klai artifact_id: knowledge-ingest calls
    ``add_episode(name=artifact_id, ...)``, so the episode node carries the
    artifact identity that makes a derived fact citable.
    """
    ep = MagicMock()
    ep.uuid = uuid
    ep.name = name
    ep.group_id = group_id
    return ep


def test_convert_results_sets_artifact_id_from_episode():
    """An edge's episode resolves to the artifact it was extracted from.

    Without this the evidence pack drops every graph edge on
    ``if not source_key: continue`` — the fact reaches the model but the
    document it came from never reaches the user.
    """
    edge = _make_graph_result("e1", "Nummerbehoud kan bij overstap", episodes=["ep-1"])

    converted = graph_search._convert_results([edge], 10, {"ep-1": "artifact-abc"})

    assert converted[0]["artifact_id"] == "artifact-abc"


def test_convert_results_keeps_artifact_id_none_when_episode_unresolved():
    """Unresolvable provenance degrades to today's behaviour, not an error."""
    edge = _make_graph_result("e1", "fact", episodes=["ep-missing"])

    converted = graph_search._convert_results([edge], 10, {})

    assert converted[0]["artifact_id"] is None


def test_convert_results_passes_through_temporal_validity():
    """Graphiti's bi-temporal fields are the one thing Qdrant cannot express.

    They were hardcoded to None at this boundary, discarding the only
    signal that distinguishes a superseded fact from a current one.
    """
    edge = _make_graph_result(
        "e1",
        "fact",
        valid_at=datetime(2026, 3, 11, tzinfo=UTC),
        invalid_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    converted = graph_search._convert_results([edge], 10, {})

    assert converted[0]["valid_at"] == "2026-03-11T00:00:00+00:00"
    assert converted[0]["invalid_at"] == "2026-08-01T00:00:00+00:00"


def test_graph_edge_with_artifact_id_becomes_citable():
    """End of the chain: the evidence pack now emits the graph fact.

    ``chunk_source_key`` falls back to ``artifact:<id>`` for URL-less
    sources, so filling artifact_id is all that stands between a graph
    fact and a citation — no evidence-pack change required.
    """
    edge = _make_graph_result("e1", "Nummerbehoud kan bij overstap", episodes=["ep-1"])
    citable = graph_search._convert_results([edge], 10, {"ep-1": "artifact-abc"})
    uncitable = graph_search._convert_results([edge], 10, {})

    pack = build_evidence_pack(citable)
    assert len(pack.items) == 1
    assert pack.items[0].chunk_id == "graph:e1"
    assert pack.items[0].artifact_id == "artifact-abc"
    assert pack.items[0].content_type == "graph_edge"
    assert [source.artifact_id for source in pack.sources] == ["artifact-abc"]
    assert chunk_source_key(citable[0]) == "artifact:artifact-abc"
    # Regression guard on the behaviour this change fixes.
    assert build_evidence_pack(uncitable).no_citable_reason == "no_citable_sources"


@pytest.mark.asyncio
async def test_episode_lookup_is_scoped_to_the_tenant_database():
    """TENANT ISOLATION: the lookup must run in the org's own FalkorDB graph.

    ``EpisodicNode.get_by_uuids`` matches on uuid ALONE — it has no group_id
    filter. Isolation therefore rests entirely on the driver being cloned to
    ``database=<org_id>``, which is the same boundary graphiti.search() uses
    via handle_multiple_group_ids. Passing the shared driver would read
    whatever database it happens to point at.
    """
    edge = _make_graph_result("e1", "fact", episodes=["ep-1"])
    cloned = MagicMock()
    mock_graphiti = AsyncMock()
    mock_graphiti.clients.driver.clone = MagicMock(return_value=cloned)

    with patch(
        "retrieval_api.services.graph_search.EpisodicNode.get_by_uuids",
        new=AsyncMock(return_value=[_make_episode("ep-1", "artifact-abc", "org-1")]),
    ) as mock_lookup:
        mapping = await graph_search._resolve_episode_artifacts(mock_graphiti, [edge], "org-1")

    mock_graphiti.clients.driver.clone.assert_called_once_with(database="org-1")
    assert mock_lookup.await_args.args[0] is cloned
    assert mapping == {"ep-1": "artifact-abc"}


@pytest.mark.asyncio
async def test_episode_from_another_tenant_is_discarded():
    """TENANT ISOLATION, defence in depth: group_id must match the caller.

    The database clone above is the real boundary. This assertion is the
    fail-closed backstop so a future Graphiti refactor that changes how the
    driver resolves databases cannot silently turn a uuid-only MATCH into a
    cross-tenant read.
    """
    edge = _make_graph_result("e1", "fact", episodes=["ep-1"])
    mock_graphiti = AsyncMock()
    mock_graphiti.clients.driver.clone = MagicMock(return_value=MagicMock())

    with patch(
        "retrieval_api.services.graph_search.EpisodicNode.get_by_uuids",
        new=AsyncMock(return_value=[_make_episode("ep-1", "artifact-of-other-org", "org-2")]),
    ):
        mapping = await graph_search._resolve_episode_artifacts(mock_graphiti, [edge], "org-1")

    assert mapping == {}


@pytest.mark.asyncio
async def test_episode_lookup_failure_keeps_graph_results():
    """Fail-open: losing provenance must not lose the graph leg itself."""
    edge = _make_graph_result("e1", "fact", episodes=["ep-1"])
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(return_value=[edge])
    mock_graphiti.clients.driver.clone = MagicMock(side_effect=RuntimeError("falkor down"))

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1", top_k=10)

    assert len(result) == 1
    assert result[0]["artifact_id"] is None


@pytest.mark.asyncio
async def test_search_resolves_provenance_end_to_end():
    """search() wires the lookup in without changing its contract."""
    edge = _make_graph_result("e1", "fact", episodes=["ep-1"])
    mock_graphiti = AsyncMock()
    mock_graphiti.search = AsyncMock(return_value=[edge])
    mock_graphiti.clients.driver.clone = MagicMock(return_value=MagicMock())

    with (
        patch("retrieval_api.services.graph_search.settings") as mock_settings,
        patch("retrieval_api.services.graph_search._get_graphiti", return_value=mock_graphiti),
        patch(
            "retrieval_api.services.graph_search.EpisodicNode.get_by_uuids",
            new=AsyncMock(return_value=[_make_episode("ep-1", "artifact-abc", "org-1")]),
        ),
    ):
        mock_settings.graphiti_enabled = True
        mock_settings.graph_search_timeout = 5.0
        result = await graph_search.search("query", "org-1", top_k=10)

    assert result[0]["artifact_id"] == "artifact-abc"


def test_graphiti_provenance_api_contract_holds():
    """Guard the real Graphiti API this feature depends on.

    Every other test here mocks the client, so a graphiti-core upgrade that
    renamed any of these would keep the suite green and silently disable
    provenance in production — the exact failure mode that shipped a broken
    Confluence connector in #1137. Assert against the real classes instead.
    """
    import inspect

    from graphiti_core import Graphiti
    from graphiti_core.driver.falkordb_driver import FalkorDriver
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EpisodicNode

    # The edge fields we read.
    for field in ("episodes", "valid_at", "invalid_at"):
        assert field in EntityEdge.model_fields, f"EntityEdge lost {field}"
    # The episode fields provenance and the tenant assertion depend on.
    for field in ("uuid", "name", "group_id"):
        assert field in EpisodicNode.model_fields, f"EpisodicNode lost {field}"
    # The tenant-scoped lookup path.
    assert "clients" in inspect.getsource(Graphiti.__init__)
    assert list(inspect.signature(FalkorDriver.clone).parameters) == ["self", "database"]
    assert list(inspect.signature(EpisodicNode.get_by_uuids).parameters) == ["driver", "uuids"]
