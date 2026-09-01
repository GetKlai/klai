"""Ingest-only Graphiti behavior layered on shared FalkorDB compatibility.

The shared package owns the two graphiti-core 0.29 runtime defects used by
both services. This module keeps only Klai's ingest semantics.
"""

from __future__ import annotations

import logging

from klai_graphiti_compat import apply_falkordb_compat

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)

_applied = False


def apply() -> None:
    """Apply shared compatibility plus ingest-specific deduplication once."""
    global _applied
    if _applied:
        return

    apply_falkordb_compat(
        initialize_databases=True,
        ann_candidate_search=settings.graph_ann_enabled,
    )
    _patch_node_dedup()
    _patch_bidirectional_edge_lookup()
    _applied = True


def _patch_node_dedup() -> None:
    """Resolve differently-cased entity names to the existing node."""
    from graphiti_core.utils.maintenance import node_operations

    original = node_operations._resolve_with_llm

    async def patched(
        llm_client,
        extracted_nodes,
        indexes,
        state,
        episode=None,
        previous_episodes=None,
        entity_types=None,
    ):
        lower_to_node = {node.name.lower(): node for node in indexes.existing_nodes}
        exact_names = {node.name for node in indexes.existing_nodes}

        result = await original(
            llm_client,
            extracted_nodes,
            indexes,
            state,
            episode=episode,
            previous_episodes=previous_episodes,
            entity_types=entity_types,
        )

        for index, resolved in enumerate(state.resolved_nodes):
            if resolved is None:
                continue
            extracted = extracted_nodes[index]
            if resolved.uuid != extracted.uuid:
                continue
            existing = lower_to_node.get(extracted.name.lower())
            if existing is None or extracted.name in exact_names:
                continue
            state.resolved_nodes[index] = existing
            state.uuid_map[extracted.uuid] = existing.uuid
            state.duplicate_pairs.append((extracted, existing))
            logger.info(
                "case_insensitive_dedup_fixed",
                extra={"extracted": extracted.name, "matched": existing.name},
            )

        return result

    node_operations._resolve_with_llm = patched
    logger.info("graphiti_node_dedup_patched")


def _patch_bidirectional_edge_lookup() -> None:
    """Consider a reversed relationship an existing dedup candidate."""
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.edges import EntityEdge, get_entity_edge_from_record
    from graphiti_core.models.edges.edge_db_queries import get_entity_edge_return_query

    original = EntityEdge.get_between_nodes

    @classmethod
    async def get_between_nodes(
        cls,
        driver,
        source_node_uuid: str,
        target_node_uuid: str,
    ):
        if driver.provider != GraphProvider.FALKORDB:
            return await original(driver, source_node_uuid, target_node_uuid)
        if driver.graph_operations_interface:
            try:
                return await driver.graph_operations_interface.edge_get_between_nodes(
                    cls, driver, source_node_uuid, target_node_uuid
                )
            except NotImplementedError:
                pass

        records, _, _ = await driver.execute_query(
            """
            MATCH (n:Entity {uuid: $source_node_uuid})-[e:RELATES_TO]-(m:Entity {uuid: $target_node_uuid})
            RETURN
            """
            + get_entity_edge_return_query(driver.provider),
            source_node_uuid=source_node_uuid,
            target_node_uuid=target_node_uuid,
            routing_="r",
        )
        return [get_entity_edge_from_record(record, driver.provider) for record in records]

    EntityEdge.get_between_nodes = get_between_nodes
    logger.info("graphiti_bidirectional_edge_lookup_patched")
