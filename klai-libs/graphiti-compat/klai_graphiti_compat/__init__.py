"""Narrow compatibility fixes for graphiti-core 0.29.x on FalkorDB.

Remove each patch when its behavior ships in the supported graphiti-core range.
The package deliberately does not replace Graphiti's group-routing or fulltext
sanitization: 0.29.3 already owns those behaviors.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

_edge_search_patched = False
_driver_clone_patched = False
_initialization_wrappers_patched = False


def apply_falkordb_compat(*, initialize_databases: bool = False) -> None:
    """Apply the remaining Graphiti 0.29 FalkorDB fixes idempotently.

    ``initialize_databases`` belongs to ingest processes. Read-only retrieval
    applies the query/clone fixes but never creates tenant indexes itself.
    """
    try:
        import graphiti_core  # noqa: F401
    except ImportError:
        logger.debug("graphiti-core not installed, skipping FalkorDB compatibility")
        return

    _patch_edge_search()
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
            task = asyncio.create_task(self.build_indices_and_constraints())
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
