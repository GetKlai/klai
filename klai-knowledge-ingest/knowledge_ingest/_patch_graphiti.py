"""Monkey-patches for graphiti-core 0.28.x bugs.

Remove this file once graphiti-core >= 0.29 ships fixes for all three issues.

Patches applied:
1. FalkorDB edge search O(n*m) timeout (getzep/graphiti#1272)
2. Node dedup case-sensitive name matching (no upstream issue)
3. FalkorDriver default_db ghost graph on init
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply() -> None:
    _patch_edge_search()
    _patch_node_dedup()
    _patch_default_db()


# ---------------------------------------------------------------------------
# 1. FalkorDB edge search: startNode(e)/endNode(e) instead of re-MATCH
#    Upstream: https://github.com/getzep/graphiti/issues/1272
# ---------------------------------------------------------------------------

def _patch_edge_search() -> None:
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
        logger.debug("graphiti-core not installed, skipping edge search patch")
        return

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
    logger.info("graphiti_edge_search_patched")


# ---------------------------------------------------------------------------
# 2. Node dedup: case-insensitive duplicate_name matching
#    The LLM returns a duplicate_name that must match an existing node name,
#    but the lookup dict at node_operations.py:311 is case-sensitive while
#    the summary lookup at :650 already uses .lower(). This causes the LLM's
#    dedup suggestion to silently fail whenever casing differs.
# ---------------------------------------------------------------------------

def _patch_node_dedup() -> None:
    try:
        from graphiti_core.utils.maintenance import node_operations
    except ImportError:
        logger.debug("graphiti-core not installed, skipping node dedup patch")
        return

    _original_escalate = node_operations._escalate_unresolved_nodes

    async def _patched_escalate(
        llm_client,
        extracted_nodes,
        indexes,
        state,
        episode=None,
        previous_episodes=None,
        entity_types=None,
    ):
        # Build case-insensitive lookup before the original runs
        lower_to_node = {
            node.name.lower(): node for node in indexes.existing_nodes
        }
        exact_names = {node.name for node in indexes.existing_nodes}

        # Run original (which may leave nodes unresolved due to case mismatch)
        result = await _original_escalate(
            llm_client,
            extracted_nodes,
            indexes,
            state,
            episode=episode,
            previous_episodes=previous_episodes,
            entity_types=entity_types,
        )

        # Post-fix: find nodes that the original left as "no duplicate" where
        # a case-insensitive match to an existing node exists
        for i, resolved in enumerate(state.resolved_nodes):
            if resolved is None:
                continue
            extracted = extracted_nodes[i]
            # Only fix nodes that resolved to themselves (= not deduped)
            if resolved.uuid != extracted.uuid:
                continue
            lower_name = extracted.name.lower()
            if lower_name in lower_to_node and extracted.name not in exact_names:
                existing = lower_to_node[lower_name]
                state.resolved_nodes[i] = existing
                state.uuid_map[extracted.uuid] = existing.uuid
                state.duplicate_pairs.append((extracted, existing))
                logger.info(
                    "case_insensitive_dedup_fixed",
                    extra={
                        "extracted": extracted.name,
                        "matched": existing.name,
                    },
                )

        return result

    node_operations._escalate_unresolved_nodes = _patched_escalate
    logger.info("graphiti_node_dedup_patched")


# ---------------------------------------------------------------------------
# 3. FalkorDriver: prevent ghost default_db graph on init
#    FalkorDriver.__init__ schedules build_indices_and_constraints() via
#    loop.create_task() on the default database. Since add_episode()
#    immediately clones to database=group_id, default_db is never used
#    but gets created with empty indexes on every restart.
# ---------------------------------------------------------------------------

def _patch_default_db() -> None:
    try:
        from graphiti_core.driver.falkordb_driver import FalkorDriver
    except ImportError:
        logger.debug("graphiti-core not installed, skipping default_db patch")
        return

    _original_init = FalkorDriver.__init__

    def _patched_init(self, *args, **kwargs):
        import asyncio

        # Temporarily suppress create_task so build_indices_and_constraints
        # doesn't run on the default database. Indices are built when
        # clone(database=group_id) is called by add_episode().
        _saved_create_task = None
        loop = None
        try:
            loop = asyncio.get_running_loop()
            _saved_create_task = loop.create_task
            loop.create_task = lambda coro: coro.close()
        except RuntimeError:
            pass

        try:
            _original_init(self, *args, **kwargs)
        finally:
            if loop is not None and _saved_create_task is not None:
                loop.create_task = _saved_create_task

    FalkorDriver.__init__ = _patched_init
    logger.info("graphiti_default_db_patch_applied")
