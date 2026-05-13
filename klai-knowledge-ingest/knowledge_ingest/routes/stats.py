"""
Stats routes:
  GET /ingest/v1/graph-stats?org_id={org_id}  - entity/edge counts from FalkorDB
  GET /ingest/v1/source-count?org_id={org_id}&kb_slug={kb_slug}  - artifact count from PostgreSQL
"""

import structlog
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from knowledge_ingest.config import settings
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.identity import assert_caller_identity_tenant_only

logger = structlog.get_logger()
router = APIRouter()

# Lazy-init FalkorDB client singleton - avoids new TCP connection per request.
_falkordb_client = None


def _get_falkordb():
    """Return the shared FalkorDB client (lazy init, process-singleton)."""
    global _falkordb_client
    if _falkordb_client is None:
        from falkordb import FalkorDB as FalkorDBClient

        _falkordb_client = FalkorDBClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
        )
    return _falkordb_client


class GraphStatsResponse(BaseModel):
    entity_count: int | None = None
    edge_count: int | None = None


class SourceCountResponse(BaseModel):
    source_count: int | None = None


@router.get("/ingest/v1/source-count", response_model=SourceCountResponse)
async def get_source_count(
    request: Request,
    org_id: str = Query(..., description="Zitadel org ID"),
    kb_slug: str = Query(..., description="Knowledge base slug"),
) -> SourceCountResponse:
    """Return the number of active source artifacts for a KB.

    SPEC-TI-003 AC-6: identity assertion on query-param org_id.
    AC-9: tenant_scoped_connection so RLS context is set for the SELECT.
    """
    try:
        verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
        async with tenant_scoped_connection(verified_org_id) as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM knowledge.artifacts "
                "WHERE org_id = $1 AND kb_slug = $2 AND superseded_by IS NULL",
                verified_org_id,
                kb_slug,
            )
        return SourceCountResponse(source_count=count)
    except Exception:
        logger.warning("stats_source_count_failed", org_id=org_id, kb_slug=kb_slug, exc_info=True)
        return SourceCountResponse()


@router.get("/ingest/v1/graph-stats", response_model=GraphStatsResponse)
async def get_graph_stats(
    request: Request,
    org_id: str = Query(..., description="Zitadel org ID"),
) -> GraphStatsResponse:
    """Return FalkorDB entity/edge counts for an org. Best-effort: returns null on failure.

    SPEC-TI-003 AC-6: identity assertion on query-param org_id.
    Note: FalkorDB is not RLS-protected; assertion is the tenant-binding here.
    """
    if not settings.graphiti_enabled:
        return GraphStatsResponse()

    try:
        verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    except Exception:
        return GraphStatsResponse()

    try:
        client = _get_falkordb()
        graph = client.select_graph(verified_org_id)

        # Count entity nodes
        try:
            entity_result = graph.query("MATCH (n:Entity) RETURN count(n) AS cnt")
            entity_count = entity_result.result_set[0][0] if entity_result.result_set else 0
        except Exception:
            entity_result = graph.query("MATCH (n) RETURN count(n) AS cnt")
            entity_count = entity_result.result_set[0][0] if entity_result.result_set else 0

        # Count relationships between nodes
        edge_result = graph.query("MATCH ()-[r]->() RETURN count(r) AS cnt")
        edge_count = edge_result.result_set[0][0] if edge_result.result_set else 0

        return GraphStatsResponse(entity_count=entity_count, edge_count=edge_count)

    except ImportError:
        logger.warning("falkordb package not available - skipping graph stats")
        return GraphStatsResponse()
    except Exception:
        logger.warning("stats_graph_stats_failed", org_id=org_id, exc_info=True)
        return GraphStatsResponse()
