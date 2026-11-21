"""A new extraction replaces the previous one only once it is written completely.

Runs the real ``ingest_graphiti_episode`` task against a real Postgres and a real
FalkorDB. Only graphiti's LLM extraction is replaced: each part writes one
episode and one fact the way graphiti stores them, or fails.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from knowledge_ingest import enrichment_tasks
from knowledge_ingest import graph as graph_module
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph
from tests.test_graph_refresh_on_stale_extraction_version import _CapturingApp


async def _run(contract: Contract, conn, monkeypatch, artifact_id: str, parts: list[str | None]):
    async def _extract(**_kwargs):
        episode_id = parts.pop(0)
        if episode_id:
            await seed_graph(contract, [episode_id], {f"{episode_id}-fact": [episode_id]})
        return episode_id

    @asynccontextmanager
    async def _tenant(_org_id: str):
        yield conn

    monkeypatch.setattr(enrichment_tasks, "tenant_scoped_connection", _tenant)
    monkeypatch.setattr(enrichment_tasks, "split_episode_text", lambda _text: ["one", "two"])
    monkeypatch.setattr(graph_module, "ingest_episode", _extract)
    monkeypatch.setattr(graph_module, "flush_entity_graph_data", AsyncMock())
    app = _CapturingApp()
    enrichment_tasks._register_tasks(app)
    await app.tasks["ingest_graphiti_episode"](
        artifact_id=artifact_id,
        org_id=contract.group_id,
        content_type="kb_article",
        belief_time_start=1,
        kb_slug="docs",
        path="page",
        replace_stale=True,
    )


async def _document(contract: Contract, insert) -> tuple[str, str]:
    await seed_graph(contract, ["previous"], {"previous-fact": ["previous"]})
    active = await insert({"document_text": "Two parts of text."}, active=True)
    previous = await insert(
        {"graphiti_episode_ids": ["previous"], "graphiti_episode_complete": True},
        active=False,
        superseded_by=active,
    )
    return active, previous


async def test_a_failed_new_extraction_keeps_every_previous_fact(
    graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, insert = tenant_rows
    active, _ = await _document(graph_helpers, insert)

    with pytest.raises(RuntimeError):
        await _run(graph_helpers, conn, monkeypatch, active, ["new-1", None])

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert episodes == {"previous", "new-1"}
    assert set(edges) == {"previous-fact", "new-1-fact"}


async def test_a_completed_new_extraction_replaces_the_previous_one(
    graph_helpers: Contract, tenant_rows, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, insert = tenant_rows
    active, previous = await _document(graph_helpers, insert)

    await _run(graph_helpers, conn, monkeypatch, active, ["new-1", "new-2"])

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert episodes == {"new-1", "new-2"}
    assert set(edges) == {"new-1-fact", "new-2-fact"}
    extra = json.loads(
        await conn.fetchval("SELECT extra::text FROM knowledge.artifacts WHERE id = $1", previous)
    )
    assert extra["graphiti_episode_ids"] == []


async def test_a_version_held_out_of_the_graph_retires_its_predecessors_and_keeps_its_marker(
    graph_helpers: Contract, tenant_rows
) -> None:
    conn, insert = tenant_rows
    await seed_graph(graph_helpers, ["previous"], {"previous-fact": ["previous"]})
    active = await insert({"graphiti_episode_id": "skipped:navigation_page"}, active=True)
    previous = await insert(
        {"graphiti_episode_ids": ["previous"]}, active=False, superseded_by=active
    )

    await graph_module.retire_earlier_extractions(conn, graph_helpers.group_id, active, keep=[])

    assert await stored_graph(graph_helpers) == (set(), {}, set())
    rows = {
        row["id"]: json.loads(row["extra"])
        for row in await conn.fetch(
            "SELECT id::text, extra::text FROM knowledge.artifacts WHERE id = ANY($1::uuid[])",
            [active, previous],
        )
    }
    assert rows[previous]["graphiti_episode_ids"] == []
    assert rows[active]["graphiti_episode_id"] == "skipped:navigation_page"
