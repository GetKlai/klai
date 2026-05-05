"""
Internal deprovisioning routes -- service-to-service only.

All routes in this module are protected by the app-level
``InternalSecretMiddleware`` (checks ``X-Internal-Secret`` on every
request). No per-route auth guard is needed.

Routes:
  POST /internal/v1/orgs/{org_id}/wipe-graph
    -- DETACH DELETE all FalkorDB nodes for the given org (tenant
      deprovisioning). Idempotent: returns 0 when graph is already empty
      or Graphiti is disabled.

  POST /internal/v1/orgs/{org_id}/wipe-postgres
    -- Hard-delete all rows carrying org_id from every knowledge.* table.
      Idempotent. Tables without an org_id column are intentionally NOT
      wiped here (artifact_entities, artifact_images, derivations,
      embedding_queue, rag_eval_results -- they are either cascade-deleted
      as children of artifacts/entities, or are shared lookup / eval tables
      with no tenant ownership column).

SPEC-INFRA-TENANT-DELETE-001 Phase 7.
SPEC-INFRA-TENANT-DELETE-002 G3.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from knowledge_ingest import graph as graph_module
from knowledge_ingest.db import get_pool

logger = structlog.get_logger()
router = APIRouter()


class WipeGraphResponse(BaseModel):
    nodes_deleted: int
    status: str


class WipePostgresResponse(BaseModel):
    rows_deleted: dict[str, int]
    status: str


# ---------------------------------------------------------------------------
# Tables that carry org_id in the knowledge schema, in FK-safe DELETE order.
#
# Leaf tables (no other knowledge.* table references them) are listed first.
# ``entities`` is listed before ``artifacts`` because artifact_entities has
# CASCADE on both artifact_id AND entity_id; deleting entities first removes
# the artifact_entities rows that would otherwise block the entities DELETE.
# ``artifacts`` is listed last: it has a self-referencing FK
# (superseded_by -> artifacts.id, NO ACTION) so we null that column for the
# org before deleting. Its child tables (artifact_entities, artifact_images,
# derivations) are all CASCADE-deleted automatically.
#
# Tables intentionally excluded (no org_id column):
#   - artifact_entities  -> CASCADE child of artifacts + entities
#   - artifact_images    -> CASCADE child of artifacts
#   - derivations        -> CASCADE child of artifacts
#   - embedding_queue    -> no FK to artifacts, no org_id; shared infra table
#   - rag_eval_results   -> eval/analytics table, no tenant ownership column
# ---------------------------------------------------------------------------
_LEAF_TABLES: list[str] = [
    "page_links",
    "crawled_pages",
    "crawl_jobs",
    "crawl_domains",
    "kb_config",
    "org_config",
    "entities",
]


async def _wipe_org_postgres(org_id: str) -> dict[str, int]:
    """Execute all DELETEs in a single transaction; return per-table row counts."""
    pool = await get_pool()
    rows_deleted: dict[str, int] = {}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Null out the self-referencing superseded_by FK in artifacts so
            # the subsequent DELETE is not blocked by NO ACTION constraint.
            await conn.execute(
                "UPDATE knowledge.artifacts SET superseded_by = NULL WHERE org_id = $1",
                org_id,
            )

            for table in _LEAF_TABLES:
                result = await conn.execute(
                    f"DELETE FROM knowledge.{table} WHERE org_id = $1",  # noqa: S608
                    org_id,
                )
                # asyncpg returns "DELETE N" as a string
                count = int(result.split()[-1])
                rows_deleted[table] = count

            # Delete artifacts last; CASCADE removes artifact_entities,
            # artifact_images, and derivations rows automatically.
            result = await conn.execute(
                "DELETE FROM knowledge.artifacts WHERE org_id = $1",
                org_id,
            )
            rows_deleted["artifacts"] = int(result.split()[-1])

    return rows_deleted


@router.post(
    "/internal/v1/orgs/{org_id}/wipe-graph",
    response_model=WipeGraphResponse,
    summary="Wipe all FalkorDB graph nodes for an org (deprovisioning)",
)
async def wipe_org_graph(org_id: str) -> WipeGraphResponse:
    """Hard-delete all FalkorDB nodes for *org_id*.

    Called by the tenant-deprovisioning orchestrator (SPEC-INFRA-TENANT-DELETE-001
    Phase 7). Idempotent: successive calls return ``nodes_deleted: 0`` after the
    first successful wipe.

    Authentication: ``X-Internal-Secret`` header, enforced by
    ``InternalSecretMiddleware`` at the app layer -- no inline check here.
    """
    logger.info("wipe_org_graph_requested", org_id=org_id)
    nodes_deleted = await asyncio.to_thread(graph_module.wipe_org_graph, org_id)
    return WipeGraphResponse(nodes_deleted=nodes_deleted, status="ok")


@router.post(
    "/internal/v1/orgs/{org_id}/wipe-postgres",
    response_model=WipePostgresResponse,
    summary="Wipe all knowledge.* Postgres rows for an org (deprovisioning)",
)
async def wipe_org_postgres(org_id: str) -> WipePostgresResponse:
    """Hard-delete every row carrying *org_id* from all knowledge.* tables.

    Called by the tenant-deprovisioning orchestrator as step 9a (SPEC
    sequencing was originally "13a" per the SPEC scope-section enumeration,
    but the actual orchestrator implementation places this immediately after
    the FalkorDB wipe at step 9, making the ordering 9a / 9b for G3 / G6
    respectively). Idempotent: successive calls return
    ``rows_deleted[table] = 0`` for all tables after the first successful wipe.

    Tables wiped (in FK-safe order):
      knowledge.page_links, knowledge.crawled_pages, knowledge.crawl_jobs,
      knowledge.crawl_domains, knowledge.kb_config, knowledge.org_config,
      knowledge.entities (CASCADE removes artifact_entities rows),
      knowledge.artifacts (CASCADE removes artifact_entities, artifact_images,
      and derivations rows).

    Tables intentionally excluded (no org_id column):
      artifact_entities, artifact_images, derivations -- cascade-deleted as
      children of artifacts or entities.
      embedding_queue, rag_eval_results -- shared infra / eval tables with no
      tenant ownership column; their lifecycle is not org-scoped.

    Authentication: ``X-Internal-Secret`` header, enforced by
    ``InternalSecretMiddleware`` at the app layer -- no inline check here.
    """
    logger.info("wipe_org_postgres_requested", org_id=org_id)
    rows_deleted = await _wipe_org_postgres(org_id)
    total = sum(rows_deleted.values())
    logger.info(
        "wipe_org_postgres_completed",
        org_id=org_id,
        total_rows_deleted=total,
        per_table=rows_deleted,
    )
    return WipePostgresResponse(rows_deleted=rows_deleted, status="ok")
