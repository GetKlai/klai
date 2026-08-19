"""Regression tests for Klai's two ingest-only Graphiti behaviors."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.edges import EntityEdge
from graphiti_core.utils.maintenance import node_operations

from knowledge_ingest import _patch_graphiti


@pytest.mark.asyncio
async def test_case_insensitive_node_dedup_reuses_existing_node(monkeypatch) -> None:
    extracted = SimpleNamespace(name="ACME", uuid="new")
    existing = SimpleNamespace(name="Acme", uuid="existing")
    indexes = SimpleNamespace(existing_nodes=[existing])
    state = SimpleNamespace(
        resolved_nodes=[None],
        uuid_map={},
        duplicate_pairs=[],
    )

    async def original(*args, **kwargs):
        state.resolved_nodes[0] = extracted
        state.uuid_map[extracted.uuid] = extracted.uuid

    monkeypatch.setattr(node_operations, "_resolve_with_llm", original)
    _patch_graphiti._patch_node_dedup()

    await node_operations._resolve_with_llm(None, [extracted], indexes, state)

    assert state.resolved_nodes == [existing]
    assert state.uuid_map == {"new": "existing"}
    assert state.duplicate_pairs == [(extracted, existing)]


@pytest.mark.asyncio
async def test_bidirectional_edge_lookup_uses_undirected_falkor_pattern() -> None:
    class Driver:
        provider = GraphProvider.FALKORDB
        graph_operations_interface = None
        captured_query = ""

        async def execute_query(self, cypher, **kwargs):
            self.captured_query = cypher
            return [], [], None

    _patch_graphiti._patch_bidirectional_edge_lookup()
    driver = Driver()

    await EntityEdge.get_between_nodes(driver, "source", "target")

    assert "-[e:RELATES_TO]-" in driver.captured_query
    assert "-[e:RELATES_TO]->" not in driver.captured_query
