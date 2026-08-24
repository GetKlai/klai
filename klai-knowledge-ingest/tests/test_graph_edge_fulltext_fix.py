"""graphiti's edge fulltext search scans every edge once per hit.

GetKlai/klai#1214. graphiti_core builds the query as

    CALL db.idx.fulltext.queryRelationships('RELATES_TO', $query)
    YIELD relationship AS rel, score
    MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity)

The MATCH re-finds a relationship the CALL already yielded, through an inline
property pattern FalkorDB does not answer from the uuid index. Cost is hits x
edges. Measured on the Voys graph (18,031 edges), same data and same term:
as shipped it timed out beyond 140 s; with startNode/endNode it took 2.99 ms.

While unpatched, every episode write into that graph failed on FalkorDB's 1 s
query timeout, and the graph silently stopped accepting knowledge after
2026-08-19.
"""

from __future__ import annotations

import pytest

from knowledge_ingest import graph as graph_module


def test_the_slow_join_is_still_what_graphiti_ships():
    """If graphiti fixes or rewrites this, the patch must stop claiming to help.

    A substring rewrite that no longer matches is a silent no-op, which is the
    failure mode this test exists to prevent.
    """
    import pathlib

    import graphiti_core.search.search_utils as search_utils

    # Read the file rather than inspect the object: other tests in this suite
    # mock parts of graphiti_core, and a mocked object would make this guard
    # pass or fail for reasons that have nothing to do with graphiti's source.
    source = pathlib.Path(search_utils.__file__).read_text(encoding="utf-8")
    assert graph_module._SLOW_EDGE_JOIN in source, (
        "graphiti no longer emits the quadratic join -- drop the patch, or "
        "update it to whatever replaced it"
    )


def test_the_replacement_binds_the_same_names():
    """The rewritten clause must define e, score, n and m for the rest of the query.

    graphiti appends `WHERE e.group_id IN $group_ids` and `WITH e, score, n, m`
    after this fragment, so anything the replacement fails to bind breaks the
    query rather than speeding it up.
    """
    fast = graph_module._FAST_EDGE_JOIN
    for name in ("rel AS e", "score", "startNode(rel) AS n", "endNode(rel) AS m"):
        assert name in fast, f"{name} is not bound by the replacement"


@pytest.mark.asyncio
async def test_the_driver_rewrites_the_query(monkeypatch):
    """The slow join must never reach FalkorDB."""
    from graphiti_core.driver import falkordb_driver

    seen: list[str] = []

    async def _original(self, cypher_query_, **kwargs):
        seen.append(cypher_query_)
        return [], None, None

    monkeypatch.setattr(falkordb_driver.FalkorDriver, "execute_query", _original)
    monkeypatch.setattr(
        falkordb_driver.FalkorDriver, "_klai_edge_join_patched", False, raising=False
    )
    graph_module._install_edge_fulltext_fix()

    await falkordb_driver.FalkorDriver.execute_query(
        object(), f"CALL something {graph_module._SLOW_EDGE_JOIN} RETURN 1"
    )

    assert seen, "the query never reached the driver"
    assert graph_module._SLOW_EDGE_JOIN not in seen[0], "the quadratic join still reaches FalkorDB"
    assert graph_module._FAST_EDGE_JOIN in seen[0]


@pytest.mark.asyncio
async def test_unrelated_queries_pass_through_untouched(monkeypatch):
    from graphiti_core.driver import falkordb_driver

    seen: list[str] = []

    async def _original(self, cypher_query_, **kwargs):
        seen.append(cypher_query_)
        return [], None, None

    monkeypatch.setattr(falkordb_driver.FalkorDriver, "execute_query", _original)
    monkeypatch.setattr(
        falkordb_driver.FalkorDriver, "_klai_edge_join_patched", False, raising=False
    )
    graph_module._install_edge_fulltext_fix()

    await falkordb_driver.FalkorDriver.execute_query(object(), "MATCH (n:Entity) RETURN n")
    assert seen[0] == "MATCH (n:Entity) RETURN n"


def test_install_is_idempotent():
    """Called on every graphiti client construction; must not stack wrappers."""
    from graphiti_core.driver import falkordb_driver

    graph_module._install_edge_fulltext_fix()
    first = falkordb_driver.FalkorDriver.execute_query
    graph_module._install_edge_fulltext_fix()
    assert falkordb_driver.FalkorDriver.execute_query is first
