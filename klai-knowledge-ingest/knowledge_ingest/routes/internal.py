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
    -- Hard-delete all rows of the org from every knowledge.* table.
      Idempotent. Child tables without an org_id column are reached
      through their parent: artifact_entities, artifact_images,
      derivations and parent_chunks by cascade, embedding_queue through
      the org's artifact ids. rag_eval_results has no tenant ownership.

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
# derivations, parent_chunks) are all CASCADE-deleted automatically, and
# crawl_job_frontier follows crawl_jobs the same way.
#
# embedding_queue has neither org_id nor a foreign key to artifacts, so its
# rows are found through the org's artifact ids and must go before them.
# rag_eval_results is an eval table without a tenant ownership column.
# ---------------------------------------------------------------------------
# Pre-formed DELETE statements indexed by table-name. Avoiding an f-string
# inside the wipe loop keeps three semgrep "asyncpg-sqli" / "raw-query"
# findings green — the table identifier is hardcoded once, never built from
# user input. The mapping is the single source of truth: any future column-
# scoping change can update the SQL here without touching call sites.
_LEAF_TABLE_DELETES: tuple[tuple[str, str], ...] = (
    (
        "embedding_queue",
        "DELETE FROM knowledge.embedding_queue WHERE artifact_id IN "
        "(SELECT id FROM knowledge.artifacts WHERE org_id = $1)",
    ),
    ("page_links", "DELETE FROM knowledge.page_links WHERE org_id = $1"),
    ("crawled_pages", "DELETE FROM knowledge.crawled_pages WHERE org_id = $1"),
    ("crawl_jobs", "DELETE FROM knowledge.crawl_jobs WHERE org_id = $1"),
    ("crawl_domains", "DELETE FROM knowledge.crawl_domains WHERE org_id = $1"),
    ("kb_config", "DELETE FROM knowledge.kb_config WHERE org_id = $1"),
    ("org_config", "DELETE FROM knowledge.org_config WHERE org_id = $1"),
    (
        "connector_resource_fences",
        "DELETE FROM knowledge.connector_resource_fences WHERE org_id = $1",
    ),
    ("graph_refresh_budget", "DELETE FROM knowledge.graph_refresh_budget WHERE org_id = $1"),
    ("entities", "DELETE FROM knowledge.entities WHERE org_id = $1"),
)

# Backwards-compat alias: the schema-regression-guard test imports
# ``_LEAF_TABLES`` to cross-check against ``information_schema.tables``.
# Derived from the canonical mapping so the two cannot drift.
_LEAF_TABLES: list[str] = [t for t, _ in _LEAF_TABLE_DELETES]


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

            for table, query in _LEAF_TABLE_DELETES:
                result = await conn.execute(query, org_id)
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

    Tables wiped (in FK-safe order): ``_LEAF_TABLE_DELETES``, then
    knowledge.artifacts (CASCADE removes artifact_entities, artifact_images,
    derivations and parent_chunks rows). rag_eval_results is not
    org-scoped.

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
