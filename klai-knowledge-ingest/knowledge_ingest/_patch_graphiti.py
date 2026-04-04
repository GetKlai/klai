"""Monkey-patch for graphiti-core FalkorDB edge search performance bug.

Upstream issue: https://github.com/getzep/graphiti/issues/1272
Upstream fix (not yet released): https://github.com/getzep/graphiti/pull/1282

The original `edge_fulltext_search` and `edge_bfs_search` methods re-MATCH
relationships by UUID after a fulltext lookup, causing an O(n*m) full graph
scan that times out on moderately-sized graphs (~1000+ RELATES_TO edges).

The fix uses `startNode(e)` / `endNode(e)` instead, which is O(n).

Remove this patch once graphiti-core >= 0.29 includes the fix.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply() -> None:
    """Patch FalkorSearchOperations.edge_fulltext_search and edge_bfs_search."""
    try:
        from graphiti_core.driver.falkordb.operations.search_ops import (
            FalkorSearchOperations,
            _build_falkor_fulltext_query,
        )
        from graphiti_core.driver.driver import GraphProvider
        from graphiti_core.driver.query_executor import QueryExecutor
        from graphiti_core.driver.record_parsers import entity_edge_from_record
        from graphiti_core.edges import EntityEdge
        from graphiti_core.graph_queries import get_relationships_query
        from graphiti_core.models.edges.edge_db_queries import (
            get_entity_edge_return_query,
        )
        from graphiti_core.search.search_filters import (
            SearchFilters,
            edge_search_filter_query_constructor,
        )
    except ImportError:
        logger.debug("graphiti-core not installed, skipping search patch")
        return

    _original_edge_fulltext = FalkorSearchOperations.edge_fulltext_search
    _original_edge_bfs = FalkorSearchOperations.edge_bfs_search

    async def _patched_edge_fulltext_search(
        self,
        executor: QueryExecutor,
        query: str,
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit: int = 10,
    ) -> list[EntityEdge]:
        fuzzy_query = _build_falkor_fulltext_query(query, group_ids)
        if fuzzy_query == "":
            return []

        filter_queries, filter_params = edge_search_filter_query_constructor(
            search_filter, GraphProvider.FALKORDB
        )

        if group_ids is not None:
            filter_queries.append("e.group_id IN $group_ids")
            filter_params["group_ids"] = group_ids

        filter_query = ""
        if filter_queries:
            filter_query = " WHERE " + (" AND ".join(filter_queries))

        cypher = (
            get_relationships_query(
                "edge_name_and_fact",
                limit=limit,
                provider=GraphProvider.FALKORDB,
            )
            + """
            YIELD relationship AS e, score
            WITH e, score, startNode(e) AS n, endNode(e) AS m
            """
            + filter_query
            + """
            RETURN
            """
            + get_entity_edge_return_query(GraphProvider.FALKORDB)
            + """
            ORDER BY score DESC
            LIMIT $limit
            """
        )

        records, _, _ = await executor.execute_query(
            cypher,
            query=fuzzy_query,
            limit=limit,
            **filter_params,
        )

        return [entity_edge_from_record(r) for r in records]

    async def _patched_edge_bfs_search(
        self,
        executor: QueryExecutor,
        origin_uuids: list[str],
        max_depth: int,
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit: int = 10,
    ) -> list[EntityEdge]:
        if not origin_uuids:
            return []

        filter_queries, filter_params = edge_search_filter_query_constructor(
            search_filter, GraphProvider.FALKORDB
        )

        if group_ids is not None:
            filter_queries.append("e.group_id IN $group_ids")
            filter_params["group_ids"] = group_ids

        cypher = (
            f"""
            UNWIND $bfs_origin_node_uuids AS origin_uuid
            MATCH path = (origin {{uuid: origin_uuid}})-[:RELATES_TO|MENTIONS*1..{max_depth}]->(:Entity)
            UNWIND relationships(path) AS rel
            WITH rel AS e, startNode(rel) AS n, endNode(rel) AS m
            WHERE type(e) = 'RELATES_TO'
            """
            + ((" AND " + " AND ".join(filter_queries)) if filter_queries else "")
            + """
            RETURN DISTINCT
            """
            + get_entity_edge_return_query(GraphProvider.FALKORDB)
            + """
            LIMIT $limit
            """
        )

        records, _, _ = await executor.execute_query(
            cypher,
            bfs_origin_node_uuids=origin_uuids,
            depth=max_depth,
            limit=limit,
            **filter_params,
        )

        return [entity_edge_from_record(r) for r in records]

    FalkorSearchOperations.edge_fulltext_search = _patched_edge_fulltext_search
    FalkorSearchOperations.edge_bfs_search = _patched_edge_bfs_search
    logger.info(
        "graphiti_search_patched",
        extra={"methods": ["edge_fulltext_search", "edge_bfs_search"]},
    )
