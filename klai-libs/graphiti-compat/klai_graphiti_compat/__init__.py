"""Narrow compatibility fixes for graphiti-core 0.29.x on FalkorDB.

Remove each patch when its behavior ships in the supported graphiti-core range.
The package deliberately does not replace Graphiti's group-routing or fulltext
sanitization: 0.29.3 already owns those behaviors.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import importlib
import inspect
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Klai uses bge-m3 embeddings for graphiti's 1024-dimensional vectors.
GRAPHITI_VECTOR_DIMENSION = 1024

_edge_search_patched = False
_vector_search_patched = False
_driver_clone_patched = False
_initialization_wrappers_patched = False
_ann_candidate_search_enabled = False


def apply_falkordb_compat(
    *, initialize_databases: bool = False, ann_candidate_search: bool = False
) -> None:
    """Apply the remaining Graphiti 0.29 FalkorDB fixes idempotently.

    ``initialize_databases`` belongs to ingest processes. Read-only retrieval
    applies the query/clone fixes but never creates tenant indexes itself.
    """
    global _ann_candidate_search_enabled
    try:
        import graphiti_core  # noqa: F401
    except ImportError:
        logger.debug("graphiti-core not installed, skipping FalkorDB compatibility")
        return

    _ann_candidate_search_enabled = _ann_candidate_search_enabled or ann_candidate_search
    _patch_edge_search()
    if ann_candidate_search:
        _patch_vector_candidate_search()
    _patch_driver_clone()
    if initialize_databases:
        _patch_database_initialization_wrappers()


def _patch_edge_search() -> None:
    global _edge_search_patched
    if _edge_search_patched:
        return

    from graphiti_core.driver.driver import GraphDriver, GraphProvider
    from graphiti_core.edges import EntityEdge, get_entity_edge_from_record
    from graphiti_core.graph_queries import get_relationships_query
    from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query
    from graphiti_core.search import search_utils
    from graphiti_core.search.search_filters import (
        SearchFilters,
        edge_search_filter_query_constructor,
    )

    original_fulltext = search_utils.edge_fulltext_search

    async def edge_fulltext_search(
        driver: GraphDriver,
        query: str,
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit: int = search_utils.RELEVANT_SCHEMA_LIMIT,
    ) -> list[EntityEdge]:
        if driver.provider != GraphProvider.FALKORDB:
            return await original_fulltext(driver, query, search_filter, group_ids, limit)
        if driver.search_interface:
            return await driver.search_interface.edge_fulltext_search(
                driver, query, search_filter, group_ids, limit
            )

        fuzzy_query = search_utils.fulltext_query(query, group_ids, driver)
        if not fuzzy_query:
            return []

        filter_queries, filter_params = edge_search_filter_query_constructor(
            search_filter, driver.provider
        )
        if group_ids is not None:
            filter_queries.append("e.group_id IN $group_ids")
            filter_params["group_ids"] = group_ids
        filter_query = " WHERE " + " AND ".join(filter_queries) if filter_queries else ""

        cypher = (
            get_relationships_query("edge_name_and_fact", limit=limit, provider=driver.provider)
            + """
            YIELD relationship AS e, score
            WITH e, score, startNode(e) AS n, endNode(e) AS m
            """
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
            cypher,
            query=fuzzy_query,
            limit=limit,
            routing_="r",
            **filter_params,
        )
        return [get_entity_edge_from_record(record, driver.provider) for record in records]

    search_utils.edge_fulltext_search = edge_fulltext_search

    # graphiti_core.search.search imports these callables at module load.
    from graphiti_core.search import search as search_module

    search_module.edge_fulltext_search = edge_fulltext_search
    _edge_search_patched = True
    logger.info("graphiti_falkor_edge_search_compat_applied")


def _patch_vector_candidate_search() -> None:
    global _vector_search_patched
    if _vector_search_patched:
        return

    from graphiti_core.driver.driver import GraphDriver, GraphProvider
    from graphiti_core.edges import EntityEdge, get_entity_edge_from_record
    from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query
    from graphiti_core.models.nodes.node_db_queries import get_entity_node_return_query
    from graphiti_core.nodes import EntityNode, get_entity_node_from_record
    from graphiti_core.search import search_utils
    from graphiti_core.search.search_filters import (
        SearchFilters,
        edge_search_filter_query_constructor,
        node_search_filter_query_constructor,
    )

    original_edge_similarity = search_utils.edge_similarity_search
    original_node_similarity = search_utils.node_similarity_search

    async def edge_similarity_search(
        driver: GraphDriver,
        search_vector: list[float],
        source_node_uuid: str | None,
        target_node_uuid: str | None,
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit: int = search_utils.RELEVANT_SCHEMA_LIMIT,
        min_score: float = search_utils.DEFAULT_MIN_SCORE,
    ) -> list[EntityEdge]:
        if driver.provider != GraphProvider.FALKORDB or driver.search_interface:
            return await original_edge_similarity(
                driver,
                search_vector,
                source_node_uuid,
                target_node_uuid,
                search_filter,
                group_ids,
                limit,
                min_score,
            )
        if search_filter.edge_uuids or source_node_uuid is not None or target_node_uuid is not None:
            return await original_edge_similarity(
                driver,
                search_vector,
                source_node_uuid,
                target_node_uuid,
                search_filter,
                group_ids,
                limit,
                min_score,
            )

        filter_queries, filter_params = edge_search_filter_query_constructor(
            search_filter, driver.provider
        )
        if filter_queries:
            return await original_edge_similarity(
                driver,
                search_vector,
                source_node_uuid,
                target_node_uuid,
                search_filter,
                group_ids,
                limit,
                min_score,
            )
        if group_ids is not None:
            filter_queries.append("e.group_id IN $group_ids")
            filter_params["group_ids"] = group_ids
        filter_query = " WHERE " + " AND ".join(filter_queries) if filter_queries else ""

        cypher = (
            """
            CALL db.idx.vector.queryRelationships('RELATES_TO', 'fact_embedding', $k, vecf32($search_vector))
            YIELD relationship AS e, score
            WITH e, startNode(e) AS n, endNode(e) AS m, (2 - score)/2 AS sim
            """
            + filter_query
            + """
            WITH e, n, m, sim AS score
            WHERE score > $min_score
            RETURN
            """
            + get_entity_edge_return_query(driver.provider)
            + """
            ORDER BY score DESC
            LIMIT $limit
            """
        )
        try:
            records, _, _ = await driver.execute_query(
                cypher,
                search_vector=search_vector,
                k=4 * limit,
                limit=limit,
                min_score=min_score,
                routing_="r",
                **filter_params,
            )
        except Exception:
            # Fall back to the brute-force original rather than fail. The
            # dominant real cause is a vector index that does not exist yet or
            # is still building ("Invalid arguments for procedure ..."):
            # retrieval-api never creates indexes, and ingest's index build on
            # an existing tenant takes longer than graphiti's retry budget —
            # without this fallback a flag flip silently drops the graph leg
            # (retrieval) or loses episodes (ingest) for exactly the window
            # REQ-5 needs to be reversible in.
            _log_vector_fallback(driver, "edge_similarity_search")
            return await original_edge_similarity(
                driver,
                search_vector,
                source_node_uuid,
                target_node_uuid,
                search_filter,
                group_ids,
                limit,
                min_score,
            )
        return [get_entity_edge_from_record(record, driver.provider) for record in records]

    async def node_similarity_search(
        driver: GraphDriver,
        search_vector: list[float],
        search_filter: SearchFilters,
        group_ids: list[str] | None = None,
        limit: int = search_utils.RELEVANT_SCHEMA_LIMIT,
        min_score: float = search_utils.DEFAULT_MIN_SCORE,
    ) -> list[EntityNode]:
        if driver.provider != GraphProvider.FALKORDB or driver.search_interface:
            return await original_node_similarity(
                driver, search_vector, search_filter, group_ids, limit, min_score
            )

        filter_queries, filter_params = node_search_filter_query_constructor(
            search_filter, driver.provider
        )
        if filter_queries:
            return await original_node_similarity(
                driver, search_vector, search_filter, group_ids, limit, min_score
            )
        if group_ids is not None:
            filter_queries.append("n.group_id IN $group_ids")
            filter_params["group_ids"] = group_ids
        filter_queries.append("score > $min_score")
        filter_query = " WHERE " + " AND ".join(filter_queries)

        cypher = (
            """
            CALL db.idx.vector.queryNodes('Entity', 'name_embedding', $k, vecf32($search_vector))
            YIELD node AS n, score
            WITH n, (2 - score)/2 AS score
            """
            + filter_query
            + """
            RETURN
            """
            + get_entity_node_return_query(driver.provider)
            + """
            ORDER BY score DESC
            LIMIT $limit
            """
        )
        try:
            records, _, _ = await driver.execute_query(
                cypher,
                search_vector=search_vector,
                k=4 * limit,
                limit=limit,
                min_score=min_score,
                routing_="r",
                **filter_params,
            )
        except Exception:
            # Same fallback rationale as the edge path above.
            _log_vector_fallback(driver, "node_similarity_search")
            return await original_node_similarity(
                driver, search_vector, search_filter, group_ids, limit, min_score
            )
        return [get_entity_node_from_record(record, driver.provider) for record in records]

    search_utils.edge_similarity_search = edge_similarity_search
    search_utils.node_similarity_search = node_similarity_search
    _rebind_graphiti_imports(
        "edge_similarity_search", original_edge_similarity, edge_similarity_search
    )
    _rebind_graphiti_imports(
        "node_similarity_search", original_node_similarity, node_similarity_search
    )

    _vector_search_patched = True
    logger.info("graphiti_falkor_vector_candidate_search_compat_applied")


# Databases for which a vector-index fallback was already logged: the fallback
# fires on every candidate search while an index is absent/building, which on
# the ingest path is dozens of times per episode — one warning per database
# per process is signal, the rest is noise.
_vector_fallback_logged: set[str] = set()


def _log_vector_fallback(driver: Any, fn_name: str) -> None:
    database = str(getattr(driver, "_database", "?"))
    if database in _vector_fallback_logged:
        return
    _vector_fallback_logged.add(database)
    logger.warning(
        "graphiti_falkor_vector_search_fell_back_to_scan "
        "database=%s fn=%s — vector index missing or still building; "
        "brute-force scan used instead (SPEC-GRAPH-SCALE-001 REQ-4)",
        database,
        fn_name,
        exc_info=True,
    )


def _rebind_graphiti_imports(name: str, original: Any, patched: Any) -> None:
    import graphiti_core

    package_roots = [str(path) for path in getattr(graphiti_core, "__path__", [])]
    for package_root in package_roots:
        for source_path in Path(package_root).rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            if name not in source:
                continue
            module_name = "graphiti_core." + str(source_path.relative_to(package_root))[
                :-3
            ].replace("/", ".")
            if module_name.endswith(".__init__"):
                module_name = module_name[: -len(".__init__")]
            try:
                importlib.import_module(module_name)
            except Exception:
                logger.debug(
                    "graphiti_vector_search_import_discovery_skipped",
                    extra={"graphiti_module": module_name},
                    exc_info=True,
                )

    for module in list(sys.modules.values()):
        if getattr(module, name, None) is original:
            setattr(module, name, patched)


async def _create_vector_indexes(driver: Any) -> None:
    for index_query in (
        f"""
        CREATE VECTOR INDEX FOR ()-[e:RELATES_TO]->() ON (e.fact_embedding)
        OPTIONS {{dimension:{GRAPHITI_VECTOR_DIMENSION}, similarityFunction:'cosine'}}
        """,
        f"""
        CREATE VECTOR INDEX FOR (n:Entity) ON (n.name_embedding)
        OPTIONS {{dimension:{GRAPHITI_VECTOR_DIMENSION}, similarityFunction:'cosine'}}
        """,
    ):
        try:
            await driver.execute_query(index_query)
        except Exception as exc:
            message = str(exc).lower()
            if "already indexed" in message or "already exists" in message:
                continue
            logger.exception("graphiti_falkor_vector_index_creation_failed")
            raise


def _patch_driver_clone() -> None:
    global _driver_clone_patched
    if _driver_clone_patched:
        return

    from graphiti_core.driver.falkordb_driver import FalkorDriver

    original_init = FalkorDriver.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Graphiti schedules schema creation from the constructor. Cancel that
        # unscoped default-db task; the ingest profile initializes the actual
        # tenant graph explicitly and retrieval remains read-only.
        init_task = getattr(self, "_init_task", None)
        if init_task is not None:
            init_task.cancel()
        self._init_task = None
        self._klai_database_init_tasks: dict[str, asyncio.Task[None]] = {}

    def patched_clone(self, database: str):
        if database == self.default_group_id:
            database = "default_db"
        if database == self._database:
            return self
        cloned = copy.copy(self)
        cloned._database = database
        cloned._init_task = None
        return cloned

    async def ensure_database_initialized(self) -> None:
        tasks = self._klai_database_init_tasks
        task = tasks.get(self._database)
        if task is None:
            task = asyncio.create_task(_initialize_database(self))
            tasks[self._database] = task
        try:
            await asyncio.shield(task)
        except Exception:
            if tasks.get(self._database) is task:
                tasks.pop(self._database, None)
            raise

    FalkorDriver.__init__ = patched_init
    FalkorDriver.clone = patched_clone
    FalkorDriver.ensure_database_initialized = ensure_database_initialized
    _driver_clone_patched = True
    logger.info("graphiti_falkor_clone_compat_applied")


async def _initialize_database(driver: Any) -> None:
    await driver.build_indices_and_constraints()
    if _ann_candidate_search_enabled:
        await _create_vector_indexes(driver)


def _wrap_database_initialization(
    method: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    from graphiti_core.driver.driver import GraphProvider

    signature = inspect.signature(method)

    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        bound = signature.bind_partial(self, *args, **kwargs)
        bound.apply_defaults()
        driver = self.clients.driver
        if driver.provider != GraphProvider.FALKORDB:
            return await method(self, *args, **kwargs)
        group_ids = bound.arguments.get("group_ids")
        group_id = bound.arguments.get("group_id")
        if "group_id" in signature.parameters:
            database_ids = [group_id or driver.default_group_id]
        else:
            database_ids = list(group_ids) if group_ids else [driver._database]
        await asyncio.gather(
            *(
                driver.clone(database=database_id).ensure_database_initialized()
                for database_id in database_ids
            )
        )
        return await method(self, *args, **kwargs)

    return wrapper


def _patch_database_initialization_wrappers() -> None:
    global _initialization_wrappers_patched
    if _initialization_wrappers_patched:
        return

    from graphiti_core.graphiti import Graphiti

    for method_name in (
        "add_episode",
        "add_episode_bulk",
        "search",
        "build_communities",
        "build_communities_with_endpoint",
        "get_all_edges",
    ):
        if hasattr(Graphiti, method_name):
            setattr(
                Graphiti,
                method_name,
                _wrap_database_initialization(getattr(Graphiti, method_name)),
            )

    _initialization_wrappers_patched = True
    logger.info("graphiti_falkor_database_initialization_compat_applied")


__all__ = ["apply_falkordb_compat"]
