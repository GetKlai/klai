"""SPEC-GRAPH-SCALE-001 REQ-1 / AC-1 — backfill.py pre-flight integration.

The pre-flight estimate must run BEFORE any episode is ingested, refuse a
build predicted to be infeasible with a non-zero exit code, allow ``--force``
to override with an explicit log line, and stay out of the way of an
ordinary small backfill.

Mocks ``knowledge_ingest.backfill._get_current_edge_count`` throughout —
these tests must not need a live FalkorDB. Follows the mocking pattern in
``tests/test_backfill_episode_text_cap.py`` /
``tests/test_backfill_kb_scope.py``: ``cross_org_admin_connection`` and
``AsyncQdrantClient`` are patched to avoid real Postgres/Qdrant, and
``ingest_episode`` is patched to avoid real graphiti/LLM calls.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import backfill, build_estimate
from knowledge_ingest.build_estimate import estimate_graph_build


def _admin_ctx(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _conn_with_one_artifact():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "artifact-1",
                "kb_slug": "support",
                "path": "guide.md",
                "content_type": "text/markdown",
                "created_at": 1,
                "extra": None,
            }
        ]
    )
    return conn


def _qdrant_with_text(text: str):
    qdrant = MagicMock()
    qdrant.scroll = AsyncMock(
        return_value=(
            [SimpleNamespace(payload={"artifact_id": "artifact-1", "text": text})],
            None,
        )
    )
    return qdrant


# Default constants: edge_ceiling = int(0.6 * 5,000,000us / (15.6us/edge * 3)) ~= 64,102.
_CEILING = estimate_graph_build(total_chars=0, current_edge_count=0).edge_ceiling
_NEAR_CEILING_EDGE_COUNT = _CEILING - 7  # a small corpus tips predicted_final_edges over
_SMALL_TEXT = "x" * 20_000  # one episode part (MAX_TEXT_CHARS=30,000), no markdown links


@pytest.mark.asyncio
async def test_corpus_above_threshold_refuses_and_never_ingests():
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock()

    # Sanity: this scenario really does predict a refusal before we assert
    # on backfill's behaviour around it.
    estimate = estimate_graph_build(
        total_chars=len(_SMALL_TEXT), current_edge_count=_NEAR_CEILING_EDGE_COUNT
    )
    assert estimate.refusal is not None

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch(
            "knowledge_ingest.backfill._get_current_edge_count",
            return_value=_NEAR_CEILING_EDGE_COUNT,
        ),
    ):
        result = await backfill.main(org_id="org-1")

    assert result != 0, "a refused build must exit non-zero"
    ingest_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_overrides_the_refusal_and_logs_the_override(caplog):
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock(return_value="episode-1")

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch(
            "knowledge_ingest.backfill._get_current_edge_count",
            return_value=_NEAR_CEILING_EDGE_COUNT,
        ),
        caplog.at_level(logging.WARNING, logger="backfill"),
    ):
        result = await backfill.main(org_id="org-1", force=True)

    assert result == 0
    ingest_episode.assert_awaited()
    override_lines = [
        record.getMessage()
        for record in caplog.records
        if "operator override" in record.getMessage()
    ]
    assert override_lines, "no operator-override line was logged"
    assert any("SPEC-GRAPH-SCALE-001" in line for line in override_lines)


@pytest.mark.asyncio
async def test_below_threshold_proceeds_without_a_force_flag():
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock(return_value="episode-1")

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch("knowledge_ingest.backfill._get_current_edge_count", return_value=0),
    ):
        result = await backfill.main(org_id="org-1")

    assert result == 0
    ingest_episode.assert_awaited()


@pytest.mark.asyncio
async def test_ann_enabled_with_indexes_allows_edge_ceiling_scenario_without_force(monkeypatch):
    monkeypatch.setattr(build_estimate.settings, "graph_ann_enabled", True)
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock(return_value="episode-1")

    estimate = estimate_graph_build(
        total_chars=len(_SMALL_TEXT), current_edge_count=_NEAR_CEILING_EDGE_COUNT
    )
    assert estimate.refusal is None
    assert estimate.predicted_final_edges > estimate.edge_ceiling

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch(
            "knowledge_ingest.backfill._get_current_edge_count",
            return_value=_NEAR_CEILING_EDGE_COUNT,
        ),
        patch("knowledge_ingest.backfill._graph_ann_indexes_operational", return_value=True),
    ):
        result = await backfill.main(org_id="org-1")

    assert result == 0
    ingest_episode.assert_awaited()


@pytest.mark.asyncio
async def test_ann_enabled_without_indexes_uses_scan_estimate_and_refuses(caplog, monkeypatch):
    monkeypatch.setattr(build_estimate.settings, "graph_ann_enabled", True)
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock()

    estimate = estimate_graph_build(
        total_chars=len(_SMALL_TEXT),
        current_edge_count=_NEAR_CEILING_EDGE_COUNT,
        ann_effective=False,
    )
    assert estimate.refusal is not None

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch(
            "knowledge_ingest.backfill._get_current_edge_count",
            return_value=_NEAR_CEILING_EDGE_COUNT,
        ),
        patch("knowledge_ingest.backfill._graph_ann_indexes_operational", return_value=False),
        caplog.at_level(logging.WARNING, logger="backfill"),
    ):
        result = await backfill.main(org_id="org-1")

    assert result != 0
    ingest_episode.assert_not_awaited()
    assert any(
        "python -m scripts.verify_graph_ann --org-id org-1" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_falkordb_count_failure_refuses_by_default():
    """The edge count must never be guessed -- an unreadable FalkorDB fails loudly."""
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock()

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch(
            "knowledge_ingest.backfill._get_current_edge_count",
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        result = await backfill.main(org_id="org-1")

    assert result != 0
    ingest_episode.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_overrides_a_falkordb_count_failure_too(caplog):
    conn = _conn_with_one_artifact()
    qdrant = _qdrant_with_text(_SMALL_TEXT)
    ingest_episode = AsyncMock(return_value="episode-1")

    with (
        patch(
            "knowledge_ingest.backfill.cross_org_admin_connection",
            return_value=_admin_ctx(conn),
        ),
        patch("knowledge_ingest.backfill.AsyncQdrantClient", return_value=qdrant),
        patch("knowledge_ingest.backfill.ingest_episode", ingest_episode),
        patch(
            "knowledge_ingest.backfill._get_current_edge_count",
            side_effect=RuntimeError("connection refused"),
        ),
        caplog.at_level(logging.WARNING, logger="backfill"),
    ):
        result = await backfill.main(org_id="org-1", force=True)

    assert result == 0
    ingest_episode.assert_awaited()
    assert any("operator override" in record.getMessage() for record in caplog.records)
