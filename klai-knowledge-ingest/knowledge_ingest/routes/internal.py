"""
Internal deprovisioning routes — service-to-service only.

All routes in this module are protected by the app-level
``InternalSecretMiddleware`` (checks ``X-Internal-Secret`` on every
request). No per-route auth guard is needed.

Routes:
  POST /internal/v1/orgs/{org_id}/wipe-graph
    — DETACH DELETE all FalkorDB nodes for the given org (tenant
      deprovisioning). Idempotent: returns 0 when graph is already empty
      or Graphiti is disabled.

SPEC-INFRA-TENANT-DELETE-001 Phase 7.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from knowledge_ingest import graph as graph_module

logger = structlog.get_logger()
router = APIRouter()


class WipeGraphResponse(BaseModel):
    nodes_deleted: int
    status: str


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
    ``InternalSecretMiddleware`` at the app layer — no inline check here.
    """
    logger.info("wipe_org_graph_requested", org_id=org_id)
    nodes_deleted = await asyncio.to_thread(graph_module.wipe_org_graph, org_id)
    return WipeGraphResponse(nodes_deleted=nodes_deleted, status="ok")
