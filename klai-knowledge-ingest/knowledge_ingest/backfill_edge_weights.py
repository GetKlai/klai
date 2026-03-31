"""Backfill RELATES_TO edge weights for all org graphs in FalkorDB.

Edge weights track how many episodes confirmed a relationship (Hebbian
reinforcement). Due to a bug in _update_edge_weights() (graph_driver vs driver),
all weights were never written. This script sets each edge's weight to the number
of Episodic nodes that connect both endpoints — equivalent to what cumulative
increments would have produced.

Run inside the knowledge-ingest container:
    python -m knowledge_ingest.backfill_edge_weights

Or on core-01:
    docker exec klai-core-knowledge-ingest-1 \
        python -m knowledge_ingest.backfill_edge_weights
"""
from __future__ import annotations

import asyncio
import sys

import structlog

try:
    import falkordb as falkordb_sync
    from graphiti_core.driver.falkordb_driver import FalkorDriver
except ImportError:
    print("graphiti-core or falkordb not installed", file=sys.stderr)
    sys.exit(1)

from knowledge_ingest.config import settings

logger = structlog.get_logger()

# Co-episode weight: weight = number of distinct Episodic nodes that mention both entities.
# Semantically equivalent to the sum of per-episode increments that should have been applied.
_WEIGHT_QUERY = (
    "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
    "WITH r, a, b "
    "MATCH (e:Episodic)--(a) WHERE (e)--(b) "
    "WITH r, count(DISTINCT e) AS co_episodes "
    "SET r.weight = co_episodes "
    "RETURN count(r) AS updated"
)


async def backfill_org(driver: FalkorDriver, org_id: str) -> int:
    result = await driver.execute_query(_WEIGHT_QUERY)
    if result is None:
        return 0
    records, _, _ = result
    return records[0].get("updated", 0) if records else 0


async def main() -> None:
    # List all graphs via the sync falkordb client (no async list API).
    client = falkordb_sync.FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)
    try:
        all_graphs: list[str] = client.list_graphs()
    finally:
        client.close()

    if not all_graphs:
        logger.info("backfill_edge_weights_no_graphs")
        return

    logger.info("backfill_edge_weights_start", graphs=all_graphs)

    total_updated = 0
    for org_id in all_graphs:
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            database=org_id,
        )
        try:
            updated = await backfill_org(driver, org_id)
            total_updated += updated
            logger.info(
                "backfill_edge_weights_org_done",
                org_id=org_id,
                edges_updated=updated,
            )
        except Exception as exc:
            logger.error(
                "backfill_edge_weights_org_failed",
                org_id=org_id,
                error=str(exc),
            )
        finally:
            await driver.close()

    logger.info("backfill_edge_weights_complete", total_edges_updated=total_updated)


if __name__ == "__main__":
    asyncio.run(main())
