"""Monkey-patches for graphiti-core 0.28.x FalkorDB bugs.

Remove this file once graphiti-core ships fixes for all issues listed below.

Patches applied:
1. Edge search O(n*m) timeout — startNode/endNode (getzep/graphiti#1272)
   Patches BOTH search_utils.py module functions AND FalkorSearchOperations methods.
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
#
#    The bug exists in TWO places:
#    a) search_utils.py — module-level functions used during add_episode (ingest)
#    b) FalkorSearchOperations — class methods (unused because search_interface=None)
#    We patch BOTH to be safe.
# ---------------------------------------------------------------------------

def _patch_edge_search() -> None:
    from graphiti_core.driver.driver import GraphDriver, GraphProvider
    from graphiti_core.edges import EntityEdge, get_entity_edge_from_record
    from graphiti_core.graph_queries import get_relationships_query
    from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query
    from graphiti_core.search.search_filters import (
        SearchFilters,
        edge_search_filter_query_constructor,
    )
    from graphiti_core.search import search_utils

    # --- Patch (a): module-level edge_fulltext_search in search_utils.py ---
    _original_fulltext = search_utils.edge_fulltext_search

    async def _patched_module_edge_fulltext_search(
        driver: GraphDriver,
        query: str,
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit=search_utils.RELEVANT_SCHEMA_LIMIT,
    ) -> list[EntityEdge]:
        # If there's a search_interface, delegate (same as original)
        if driver.search_interface:
            return await driver.search_interface.edge_fulltext_search(
                driver, query, search_filter, group_ids, limit
            )

        fuzzy_query = search_utils.fulltext_query(query, group_ids, driver)
        if fuzzy_query == '':
            return []

        # FIX: use startNode/endNode instead of re-MATCH
        if driver.provider == GraphProvider.FALKORDB:
            match_query = """
    YIELD relationship AS e, score
    WITH e, score, startNode(e) AS n, endNode(e) AS m
    """
        elif driver.provider == GraphProvider.KUZU:
            match_query = """
        YIELD node, score
        MATCH (n:Entity)-[:RELATES_TO]->(e:RelatesToNode_ {uuid: node.uuid})-[:RELATES_TO]->(m:Entity)
        """
        else:
            # Neo4j — original pattern is fine (fast with native indexes)
            match_query = """
    YIELD relationship AS rel, score
    MATCH (n:Entity)-[e:RELATES_TO {uuid: rel.uuid}]->(m:Entity)
    """

        filter_queries, filter_params = edge_search_filter_query_constructor(
            search_filter, driver.provider
        )
        if group_ids is not None:
            filter_queries.append('e.group_id IN $group_ids')
            filter_params['group_ids'] = group_ids

        filter_query = ''
        if filter_queries:
            filter_query = ' WHERE ' + (' AND '.join(filter_queries))

        cypher = (
            get_relationships_query('edge_name_and_fact', limit=limit, provider=driver.provider)
            + match_query
            + filter_query
            + """
            RETURN
            """
            + get_entity_edge_return_query(driver.provider)
            + """
            ORDER BY score DESC
            LIMIT $limit
            """
        )
        records, _, _ = await driver.execute_query(
            cypher, query=fuzzy_query, limit=limit, routing_='r', **filter_params,
        )
        return [get_entity_edge_from_record(r, driver.provider) for r in records]

    # --- Patch (a): module-level edge_bfs_search ---
    _original_bfs = search_utils.edge_bfs_search

    async def _patched_module_edge_bfs_search(
        driver: GraphDriver,
        bfs_origin_node_uuids: list[str] | None,
        bfs_max_depth: int,
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit: int = search_utils.RELEVANT_SCHEMA_LIMIT,
    ) -> list[EntityEdge]:
        if driver.search_interface:
            try:
                return await driver.search_interface.edge_bfs_search(
                    driver, bfs_origin_node_uuids, bfs_max_depth, search_filter, group_ids, limit
                )
            except NotImplementedError:
                pass

        if bfs_origin_node_uuids is None or len(bfs_origin_node_uuids) == 0:
            return []

        filter_queries, filter_params = edge_search_filter_query_constructor(
            search_filter, driver.provider
        )
        if group_ids is not None:
            filter_queries.append('e.group_id IN $group_ids')
            filter_params['group_ids'] = group_ids

        filter_query = ''
        if filter_queries:
            filter_query = ' WHERE ' + (' AND '.join(filter_queries))

        # FIX: use startNode/endNode for FalkorDB
        if driver.provider == GraphProvider.FALKORDB:
            query = (
                f"""
                UNWIND $bfs_origin_node_uuids AS origin_uuid
                MATCH path = (origin {{uuid: origin_uuid}})-[:RELATES_TO|MENTIONS*1..{bfs_max_depth}]->(:Entity)
                UNWIND relationships(path) AS rel
                WITH rel AS e, startNode(rel) AS n, endNode(rel) AS m
                WHERE type(e) = 'RELATES_TO'
                """
                + (' AND ' + ' AND '.join(filter_queries) if filter_queries else '')
                + """
                RETURN DISTINCT
                """
                + get_entity_edge_return_query(driver.provider)
                + """
                LIMIT $limit
                """
            )
        else:
            # Neo4j / other — use original pattern
            query = (
                f"""
                UNWIND $bfs_origin_node_uuids AS origin_uuid
                MATCH path = (origin {{uuid: origin_uuid}})-[:RELATES_TO|MENTIONS*1..{bfs_max_depth}]->(:Entity)
                UNWIND relationships(path) AS rel
                MATCH (n:Entity)-[e:RELATES_TO {{uuid: rel.uuid}}]-(m:Entity)
                """
                + filter_query
                + """
                RETURN DISTINCT
                """
                + get_entity_edge_return_query(driver.provider)
                + """
                LIMIT $limit
                """
            )

        records, _, _ = await driver.execute_query(
            query, bfs_origin_node_uuids=bfs_origin_node_uuids, depth=bfs_max_depth,
            limit=limit, routing_='r', **filter_params,
        )
        return [get_entity_edge_from_record(r, driver.provider) for r in records]

    # Apply module-level patches
    search_utils.edge_fulltext_search = _patched_module_edge_fulltext_search
    search_utils.edge_bfs_search = _patched_module_edge_bfs_search

    # Also patch the import in search.py (it imports at module level)
    from graphiti_core.search import search as search_module
    search_module.edge_fulltext_search = _patched_module_edge_fulltext_search
    search_module.edge_bfs_search = _patched_module_edge_bfs_search

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

    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core import decorators
    from graphiti_core.helpers import semaphore_gather
    from graphiti_core.search.search_config import SearchResults

    def _get_parameter_position(func, param_name: str) -> int | None:
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

    decorators.handle_multiple_group_ids = handle_multiple_group_ids

    try:
        from graphiti_core.graphiti import Graphiti
        for method_name in ['search', 'build_communities', 'build_communities_with_endpoint',
                            'get_all_edges']:
            if hasattr(Graphiti, method_name):
                method = getattr(Graphiti, method_name)
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
    from graphiti_core.driver.driver import GraphDriver
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

    from graphiti_core.utils.maintenance import edge_operations

    _original_resolve = edge_operations.resolve_extracted_edges

    async def _patched_resolve(clients, extracted_edges, existing_edges, episode, nodes,
                               previous_episodes=None, entity_types=None):
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
#    Patches the module-level fulltext_query() in search_utils.py
# ---------------------------------------------------------------------------

def _patch_empty_fulltext_guard() -> None:
    from graphiti_core.search import search_utils

    _original_fulltext_query = search_utils.fulltext_query

    def _patched_fulltext_query(query, group_ids, driver):
        result = _original_fulltext_query(query, group_ids, driver)
        # Guard: if all query words were stopwords, the result may be
        # "(@group_id:...) ()" which is invalid RediSearch syntax.
        if result and result.endswith('()'):
            return ''
        return result

    search_utils.fulltext_query = _patched_fulltext_query

    # Also patch in search.py which imports it
    from graphiti_core.search import search as search_module
    if hasattr(search_module, 'fulltext_query'):
        search_module.fulltext_query = _patched_fulltext_query

    logger.info("graphiti_empty_fulltext_guard_patched")
