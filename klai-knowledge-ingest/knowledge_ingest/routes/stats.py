"""
Stats routes:
  GET /ingest/v1/graph-stats?org_id={org_id}  — entity/edge counts from FalkorDB
  GET /ingest/v1/source-count?org_id={org_id}&kb_slug={kb_slug}  — artifact count from PostgreSQL
"""
import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from knowledge_ingest import db
from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class GraphStatsResponse(BaseModel):
    entity_count: int | None = None
    edge_count: int | None = None


class SourceCountResponse(BaseModel):
    source_count: int | None = None


@router.get("/ingest/v1/source-count", response_model=SourceCountResponse)
async def get_source_count(
    org_id: str = Query(..., description="Zitadel org ID"),
    kb_slug: str = Query(..., description="Knowledge base slug"),
) -> SourceCountResponse:
    """Return the number of active source artifacts for a KB."""
    try:
        async with db.async_session() as session:
            result = await session.execute(
                text(
                    "SELECT COUNT(*) FROM knowledge.artifacts "
                    "WHERE org_id = :org_id AND kb_slug = :kb_slug AND status = 'active'"
                ),
                {"org_id": org_id, "kb_slug": kb_slug},
            )
            count = result.scalar_one()
            return SourceCountResponse(source_count=count)
    except Exception as exc:
        logger.debug("Could not fetch source count for org=%s kb=%s: %s", org_id, kb_slug, exc)
        return SourceCountResponse()


@router.get("/ingest/v1/graph-stats", response_model=GraphStatsResponse)
async def get_graph_stats(org_id: str = Query(..., description="Zitadel org ID")) -> GraphStatsResponse:
    """Return FalkorDB entity/edge counts for an org. Best-effort: returns null on failure."""
    if not settings.graphiti_enabled:
        return GraphStatsResponse()

    try:
        from falkordb import FalkorDB as FalkorDBClient

        client = FalkorDBClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
        )

        # Graphiti stores data in a graph named "graphiti_{group_id}"
        graph_name = f"graphiti_{org_id}"

        try:
            graph = client.select_graph(graph_name)
        except Exception:
            # Graph doesn't exist yet for this org
            return GraphStatsResponse(entity_count=0, edge_count=0)

        # Count entity nodes — Graphiti uses EntityNode (label "Entity") for extracted
        # concepts; EpisodeNode (label "Episodic") for ingest metadata.
        # Try Entity label first; fall back to counting all nodes if label doesn't exist.
        try:
            entity_result = graph.query(
                "MATCH (n:Entity) RETURN count(n) AS cnt"
            )
            entity_count = entity_result.result_set[0][0] if entity_result.result_set else 0
        except Exception:
            entity_result = graph.query(
                "MATCH (n) RETURN count(n) AS cnt"
            )
            entity_count = entity_result.result_set[0][0] if entity_result.result_set else 0

        # Count relationships between nodes
        edge_result = graph.query(
            "MATCH ()-[r]->() RETURN count(r) AS cnt"
        )
        edge_count = edge_result.result_set[0][0] if edge_result.result_set else 0

        return GraphStatsResponse(entity_count=entity_count, edge_count=edge_count)

    except ImportError:
        logger.debug("falkordb package not available — skipping graph stats")
        return GraphStatsResponse()
    except Exception as exc:
        logger.debug("Could not fetch graph stats for org %s: %s", org_id, exc)
        return GraphStatsResponse()
