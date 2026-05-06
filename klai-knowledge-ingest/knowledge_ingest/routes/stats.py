"""
Stats routes:
  GET /ingest/v1/graph-stats?org_id={org_id}  — entity/edge counts from FalkorDB
  GET /ingest/v1/source-count?org_id={org_id}&kb_slug={kb_slug}  — artifact count from PostgreSQL

SPEC-TI-010C B-8: Both endpoints now enforce X-Caller-Service header in addition to
the existing X-Internal-Secret check (InternalSecretMiddleware). Unknown callers get 403.
"""
import structlog
from fastapi import APIRouter, Header, HTTPException, Query, status
from klai_identity_assert import KNOWN_CALLER_SERVICES
from pydantic import BaseModel

from knowledge_ingest import db
from knowledge_ingest.config import settings

logger = structlog.get_logger()
router = APIRouter()


# @MX:ANCHOR: [AUTO] Identity-assertion guard for stats endpoints
# @MX:REASON: SPEC-TI-010C B-8 — stats endpoints must verify caller service identity
# @MX:SPEC: SPEC-TI-010C B-8
def _require_caller_service(x_caller_service: str | None) -> str:
    """Enforce X-Caller-Service is present and is a known service.

    Raises HTTP 403 for absent or unknown values.
    Returns the validated caller service name.
    """
    if not x_caller_service:
        logger.warning("stats_missing_caller_service_header")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Caller-Service header required",
        )
    if x_caller_service not in KNOWN_CALLER_SERVICES:
        logger.warning("stats_unknown_caller_service", caller=x_caller_service)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unknown caller service",
        )
    return x_caller_service

# Lazy-init FalkorDB client singleton — avoids new TCP connection per request.
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
    org_id: str = Query(..., description="Zitadel org ID"),
    kb_slug: str = Query(..., description="Knowledge base slug"),
    x_caller_service: str | None = Header(default=None, alias="X-Caller-Service"),
) -> SourceCountResponse:
    """Return the number of active source artifacts for a KB."""
    _require_caller_service(x_caller_service)
    try:
        pool = await db.get_pool()
        count = await pool.fetchval(
            "SELECT COUNT(*) FROM knowledge.artifacts "
            "WHERE org_id = $1 AND kb_slug = $2 AND superseded_by IS NULL",
            org_id, kb_slug,
        )
        return SourceCountResponse(source_count=count)
    except Exception as exc:
        logger.warning("stats_source_count_failed", org_id=org_id, kb_slug=kb_slug, error=str(exc))
        return SourceCountResponse()


@router.get("/ingest/v1/graph-stats", response_model=GraphStatsResponse)
async def get_graph_stats(
    org_id: str = Query(..., description="Zitadel org ID"),
    x_caller_service: str | None = Header(default=None, alias="X-Caller-Service"),
) -> GraphStatsResponse:
    """Return FalkorDB entity/edge counts for an org. Best-effort: returns null on failure."""
    _require_caller_service(x_caller_service)
    if not settings.graphiti_enabled:
        return GraphStatsResponse()

    try:
        client = _get_falkordb()
        graph = client.select_graph(org_id)

        # Count entity nodes — Graphiti uses EntityNode (label "Entity") for extracted
        # concepts; EpisodeNode (label "Episodic") for ingest metadata.
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
        logger.warning("falkordb package not available — skipping graph stats")
        return GraphStatsResponse()
    except Exception as exc:
        logger.warning("stats_graph_stats_failed", org_id=org_id, error=str(exc))
        return GraphStatsResponse()
