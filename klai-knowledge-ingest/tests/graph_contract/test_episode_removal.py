"""Removing episodes from a real FalkorDB graph removes the facts only they back.

Retrieval searches ``RELATES_TO`` edges, not episodes, so an edge that keeps
citing a deleted episode stays searchable. These tests read the stored graph
after the helper ran; see conftest.py for how the target server is chosen.
"""

from __future__ import annotations

import pytest

from knowledge_ingest import graph as graph_module
from tests.graph_contract.conftest import Contract, seed_graph, stored_graph


async def test_removed_episodes_take_the_facts_only_they_back(graph_helpers: Contract) -> None:
    await seed_graph(
        graph_helpers,
        ["stale", "kept"],
        {"only-stale": ["stale"], "shared": ["stale", "kept"], "only-kept": ["kept"]},
    )

    await graph_module.delete_kb_episodes(graph_helpers.group_id, ["stale"])

    episodes, edges, entities = await stored_graph(graph_helpers)
    assert episodes == {"kept"}
    assert edges == {"shared": ["kept"], "only-kept": ["kept"]}
    assert entities == {"shared-source", "shared-target", "only-kept-source", "only-kept-target"}


async def test_a_replaced_fact_goes_while_the_current_version_still_names_its_entities(
    graph_helpers: Contract,
) -> None:
    """The usual case after a re-extraction: same entities, the old statement about them."""
    await seed_graph(graph_helpers, ["old", "new"], {"old-fact": ["old"]})
    driver = graph_helpers.graphiti.driver.clone(database=graph_helpers.group_id)
    await driver.execute_query(
        "MATCH (e:Episodic {uuid: 'new'}), (n:Entity) CREATE (e)-[:MENTIONS]->(n)"
    )

    await graph_module.delete_kb_episodes(graph_helpers.group_id, ["old"])

    episodes, edges, entities = await stored_graph(graph_helpers)
    assert episodes == {"new"}
    assert edges == {}
    assert entities == {"old-fact-source", "old-fact-target"}


async def test_an_edge_cited_by_episodes_in_different_batches_goes(
    graph_helpers: Contract, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(graph_module, "_EPISODE_DELETE_BATCH", 2)
    await seed_graph(
        graph_helpers,
        ["a", "b", "c", "d", "kept"],
        {"first-and-last": ["a", "d"], "kept": ["c", "kept"]},
    )

    await graph_module.delete_kb_episodes(graph_helpers.group_id, ["a", "b", "c", "d"])

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert episodes == {"kept"}
    assert edges == {"kept": ["kept"]}


async def test_removing_a_graphiti_written_episode_leaves_nothing_behind(
    graph_helpers: Contract,
) -> None:
    """The same outcome on an episode graphiti itself extracted, not a hand-built one."""
    await graph_helpers.ingest("extract", ["Meridian Logistics", "Harbor Freight Co"])
    episodes, edges, entities = await stored_graph(graph_helpers)
    assert edges and entities

    await graph_module.delete_kb_episodes(graph_helpers.group_id, sorted(episodes))

    assert await stored_graph(graph_helpers) == (set(), {}, set())


async def test_org_wide_sweep_removes_what_is_not_alive_with_its_facts(
    graph_helpers: Contract,
) -> None:
    await seed_graph(
        graph_helpers,
        ["replaced", "current"],
        {"replaced-fact": ["replaced"], "shared": ["replaced", "current"]},
    )

    deleted = await graph_module.sweep_orphan_episodes_org_wide(graph_helpers.group_id, {"current"})

    episodes, edges, _ = await stored_graph(graph_helpers)
    assert deleted == 1
    assert episodes == {"current"}
    assert edges == {"shared": ["current"]}


async def test_an_entity_another_job_has_not_linked_yet_survives(graph_helpers: Contract) -> None:
    """Graphiti saves an entity before the MENTIONS that link it; removing an
    unrelated episode in between must not delete it."""
    await seed_graph(graph_helpers, ["stale"], {"stale-fact": ["stale"]})
    driver = graph_helpers.graphiti.driver.clone(database=graph_helpers.group_id)
    await driver.execute_query(
        "CREATE (:Entity {uuid: 'just-merged', group_id: $g})", g=graph_helpers.group_id
    )

    await graph_module.delete_kb_episodes(graph_helpers.group_id, ["stale"])

    _, _, entities = await stored_graph(graph_helpers)
    assert entities == {"just-merged"}
