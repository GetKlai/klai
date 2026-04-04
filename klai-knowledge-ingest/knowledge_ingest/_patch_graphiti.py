"""Monkey-patches for graphiti-core 0.28.x FalkorDB bugs.

Remove this file once graphiti-core ships fixes for all issues listed below.

Patches applied:
1. Edge search O(n*m) timeout — startNode/endNode (getzep/graphiti#1272)
2. Node dedup case-sensitive name matching (no upstream issue)
3. Decorator single group_id routing (getzep/graphiti#1305, #1326)
4. FalkorDriver.clone race condition — copy.copy instead of __init__ (#1305)
5. Bidirectional edge dedup (getzep/graphiti#1303)
6. Empty fulltext query guard (getzep/graphiti#1375)
"""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)


def apply() -> None:
    try:
        import graphiti_core  # noqa: F401
    except ImportError:
        logger.debug("graphiti-core not installed, skipping all patches")
        return

    _patch_edge_search()
    _patch_node_dedup()
    _patch_driver_clone()
    _patch_decorator_routing()
    _patch_bidirectional_edge_dedup()
    _patch_empty_fulltext_guard()


# ---------------------------------------------------------------------------
# 1. FalkorDB edge search: startNode(e)/endNode(e) instead of re-MATCH
#    Upstream: https://github.com/getzep/graphiti/issues/1272
# ---------------------------------------------------------------------------

def _patch_edge_search() -> None:
    from graphiti_core.driver.falkordb.operations.search_ops import (
        FalkorSearchOperations,
        _build_falkor_fulltext_query,
    )
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.driver.query_executor import QueryExecutor
    from graphiti_core.driver.record_parsers import entity_edge_from_record
    from graphiti_core.edges import EntityEdge
    from graphiti_core.graph_queries import get_relationships_query
    from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query
    from graphiti_core.search.search_filters import (
        SearchFilters,
        edge_search_filter_query_constructor,
    )

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
                "edge_name_and_fact", limit=limit, provider=GraphProvider.FALKORDB,
            )
            + "\n            YIELD relationship AS e, score"
            + "\n            WITH e, score, startNode(e) AS n, endNode(e) AS m"
            + filter_query
            + "\n            RETURN\n            "
            + get_entity_edge_return_query(GraphProvider.FALKORDB)
            + "\n            ORDER BY score DESC\n            LIMIT $limit"
        )
        records, _, _ = await executor.execute_query(
            cypher, query=fuzzy_query, limit=limit, **filter_params,
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

        extra_filter = (" AND " + " AND ".join(filter_queries)) if filter_queries else ""

        cypher = (
            f"""
            UNWIND $bfs_origin_node_uuids AS origin_uuid
            MATCH path = (origin {{uuid: origin_uuid}})-[:RELATES_TO|MENTIONS*1..{max_depth}]->(:Entity)
            UNWIND relationships(path) AS rel
            WITH rel AS e, startNode(rel) AS n, endNode(rel) AS m
            WHERE type(e) = 'RELATES_TO'"""
            + extra_filter
            + "\n            RETURN DISTINCT\n            "
            + get_entity_edge_return_query(GraphProvider.FALKORDB)
            + "\n            LIMIT $limit"
        )
        records, _, _ = await executor.execute_query(
            cypher, bfs_origin_node_uuids=origin_uuids, depth=max_depth, limit=limit,
            **filter_params,
        )
        return [entity_edge_from_record(r) for r in records]

    FalkorSearchOperations.edge_fulltext_search = _patched_edge_fulltext_search
    FalkorSearchOperations.edge_bfs_search = _patched_edge_bfs_search
    logger.info("graphiti_edge_search_patched")


# ---------------------------------------------------------------------------
# 2. Node dedup: case-insensitive duplicate_name matching
# ---------------------------------------------------------------------------

def _patch_node_dedup() -> None:
    from graphiti_core.utils.maintenance import node_operations

    _original = node_operations._resolve_with_llm

    async def _patched(llm_client, extracted_nodes, indexes, state,
                       episode=None, previous_episodes=None, entity_types=None):
        lower_to_node = {n.name.lower(): n for n in indexes.existing_nodes}
        exact_names = {n.name for n in indexes.existing_nodes}

        result = await _original(
            llm_client, extracted_nodes, indexes, state,
            episode=episode, previous_episodes=previous_episodes,
            entity_types=entity_types,
        )

        for i, resolved in enumerate(state.resolved_nodes):
            if resolved is None:
                continue
            extracted = extracted_nodes[i]
            if resolved.uuid != extracted.uuid:
                continue
            lower_name = extracted.name.lower()
            if lower_name in lower_to_node and extracted.name not in exact_names:
                existing = lower_to_node[lower_name]
                state.resolved_nodes[i] = existing
                state.uuid_map[extracted.uuid] = existing.uuid
                state.duplicate_pairs.append((extracted, existing))
                logger.info("case_insensitive_dedup_fixed",
                            extra={"extracted": extracted.name, "matched": existing.name})

        return result

    node_operations._resolve_with_llm = _patched
    logger.info("graphiti_node_dedup_patched")


# ---------------------------------------------------------------------------
# 3. FalkorDriver.clone: use copy.copy instead of calling __init__
#    Prevents ghost default_db graph creation and race conditions.
#    From getzep/graphiti#1305
# ---------------------------------------------------------------------------

def _patch_driver_clone() -> None:
    from graphiti_core.driver.falkordb_driver import FalkorDriver

    # Add _initialized_databases tracking to existing instances
    _original_init = FalkorDriver.__init__

    def _patched_init(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        if not hasattr(self, '_initialized_databases'):
            self._initialized_databases: set[str] = set()
        self._initialized_databases.add(self._database)

    def _patched_clone(self, database: str):
        if database == self._database:
            return self
        cloned = copy.copy(self)
        cloned._database = database
        return cloned

    async def _ensure_database_initialized(self) -> None:
        if not hasattr(self, '_initialized_databases'):
            self._initialized_databases = set()
        if self._database not in self._initialized_databases:
            self._initialized_databases.add(self._database)
            await self.build_indices_and_constraints()

    FalkorDriver.__init__ = _patched_init
    FalkorDriver.clone = _patched_clone
    FalkorDriver.ensure_database_initialized = _ensure_database_initialized
    logger.info("graphiti_driver_clone_patched")


# ---------------------------------------------------------------------------
# 4. Decorator: route single group_id to correct FalkorDB graph
#    The original only routes when len(group_ids) > 1, missing the common
#    single-tenant case entirely. From getzep/graphiti#1305, #1326
# ---------------------------------------------------------------------------

def _patch_decorator_routing() -> None:
    import functools
    from collections.abc import Awaitable, Callable
    from typing import Any

    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core import decorators
    from graphiti_core.helpers import semaphore_gather
    from graphiti_core.search.search_config import SearchResults

    def _get_parameter_position(func: Callable, param_name: str) -> int | None:
        import inspect
        sig = inspect.signature(func)
        for idx, (name, _) in enumerate(sig.parameters.items()):
            if name == param_name:
                return idx
        return None

    def handle_multiple_group_ids(func):
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            group_ids_func_pos = _get_parameter_position(func, 'group_ids')
            group_ids_pos = (group_ids_func_pos - 1 if group_ids_func_pos is not None else None)
            group_ids = kwargs.get('group_ids')

            if group_ids is None and group_ids_pos is not None and len(args) > group_ids_pos:
                group_ids = args[group_ids_pos]

            # Route ALL group_ids for FalkorDB (not just len > 1)
            if (
                hasattr(self, 'clients')
                and hasattr(self.clients, 'driver')
                and self.clients.driver.provider == GraphProvider.FALKORDB
                and group_ids
            ):
                driver = self.clients.driver

                async def execute_for_group(gid: str):
                    filtered_args = list(args)
                    if group_ids_pos is not None and len(args) > group_ids_pos:
                        filtered_args.pop(group_ids_pos)

                    cloned = driver.clone(database=gid)
                    if hasattr(cloned, 'ensure_database_initialized'):
                        await cloned.ensure_database_initialized()

                    return await func(
                        self, *filtered_args,
                        **{**kwargs, 'group_ids': [gid], 'driver': cloned},
                    )

                results = await semaphore_gather(
                    *[execute_for_group(gid) for gid in group_ids],
                    max_coroutines=getattr(self, 'max_coroutines', None),
                )

                if isinstance(results[0], SearchResults):
                    return SearchResults.merge(results)
                elif isinstance(results[0], list):
                    return [item for result in results for item in result]
                elif isinstance(results[0], tuple):
                    merged = []
                    for i in range(len(results[0])):
                        components = [result[i] for result in results]
                        if isinstance(components[0], list):
                            merged.append([item for c in components for item in c])
                        else:
                            merged.append(components)
                    return tuple(merged)
                else:
                    return results

            return await func(self, *args, **kwargs)

        return wrapper

    # Replace the decorator in the module so new usages pick it up
    decorators.handle_multiple_group_ids = handle_multiple_group_ids

    # Re-decorate already-imported Graphiti methods that use this decorator
    try:
        from graphiti_core.graphiti import Graphiti
        for method_name in ['search', 'build_communities', 'build_communities_with_endpoint',
                            'get_all_edges']:
            if hasattr(Graphiti, method_name):
                method = getattr(Graphiti, method_name)
                # Unwrap the original function from the old decorator
                original = getattr(method, '__wrapped__', method)
                setattr(Graphiti, method_name, handle_multiple_group_ids(original))
    except Exception as e:
        logger.warning("could not re-decorate Graphiti methods: %s", e)

    logger.info("graphiti_decorator_routing_patched")


# ---------------------------------------------------------------------------
# 5. Bidirectional edge dedup
#    Edge dedup only searched forward direction, missing reversed duplicates.
#    From getzep/graphiti#1303
# ---------------------------------------------------------------------------

def _patch_bidirectional_edge_dedup() -> None:
    from graphiti_core.edges import EntityEdge
    from graphiti_core.driver.driver import GraphDriver, GraphProvider
    from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query
    from graphiti_core.driver.record_parsers import entity_edge_from_record

    @classmethod  # type: ignore[misc]
    async def get_between_nodes_bidirectional(
        cls, driver: GraphDriver, node_uuid_a: str, node_uuid_b: str
    ):
        match_query = """
            MATCH (n:Entity {uuid: $node_uuid_a})-[e:RELATES_TO]-(m:Entity {uuid: $node_uuid_b})
        """
        records, _, _ = await driver.execute_query(
            match_query + "\n            RETURN\n            "
            + get_entity_edge_return_query(driver.provider),
            node_uuid_a=node_uuid_a,
            node_uuid_b=node_uuid_b,
            routing_='r',
        )
        return [entity_edge_from_record(r) for r in records]

    EntityEdge.get_between_nodes_bidirectional = get_between_nodes_bidirectional

    # Patch resolve_extracted_edges to use bidirectional search
    from graphiti_core.utils.maintenance import edge_operations
    import inspect

    src = inspect.getsource(edge_operations.resolve_extracted_edges)
    if 'get_between_nodes_bidirectional' not in src:
        from graphiti_core.helpers import semaphore_gather

        _original_resolve = edge_operations.resolve_extracted_edges

        async def _patched_resolve(clients, extracted_edges, existing_edges, episode, nodes,
                                   previous_episodes=None, entity_types=None):
            # The original calls get_between_nodes (forward only).
            # We wrap it to use bidirectional instead by temporarily
            # pointing get_between_nodes to the bidirectional version.
            _original_get = EntityEdge.get_between_nodes
            EntityEdge.get_between_nodes = EntityEdge.get_between_nodes_bidirectional
            try:
                return await _original_resolve(
                    clients, extracted_edges, existing_edges, episode, nodes,
                    previous_episodes=previous_episodes, entity_types=entity_types,
                )
            finally:
                EntityEdge.get_between_nodes = _original_get

        edge_operations.resolve_extracted_edges = _patched_resolve

    logger.info("graphiti_bidirectional_edge_dedup_patched")


# ---------------------------------------------------------------------------
# 6. Empty fulltext query guard
#    Stopword-only queries produce invalid RediSearch syntax.
#    From getzep/graphiti#1375
# ---------------------------------------------------------------------------

def _patch_empty_fulltext_guard() -> None:
    from graphiti_core.driver.falkordb.operations.search_ops import (
        FalkorSearchOperations,
        _sanitize,
        MAX_QUERY_LENGTH,
    )
    from graphiti_core.driver.falkordb import STOPWORDS

    def _patched_build_fulltext_query(
        self, query: str, group_ids: list[str] | None = None,
        max_query_length: int = MAX_QUERY_LENGTH,
    ) -> str:
        if group_ids is None or len(group_ids) == 0:
            group_filter = ''
        else:
            escaped = [f'"{gid}"' for gid in group_ids]
            group_filter = f'(@group_id:{"| ".join(escaped)})'

        sanitized = _sanitize(query)
        words = sanitized.split()
        filtered = [w for w in words if w and w.lower() not in STOPWORDS]
        sanitized_query = ' | '.join(filtered)

        if not sanitized_query:
            return ''

        if len(sanitized_query.split(' ')) + len(group_ids or '') >= max_query_length:
            return ''

        return group_filter + ' (' + sanitized_query + ')'

    FalkorSearchOperations.build_fulltext_query = _patched_build_fulltext_query
    logger.info("graphiti_empty_fulltext_guard_patched")
