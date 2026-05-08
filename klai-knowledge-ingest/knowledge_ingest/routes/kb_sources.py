"""KB sources routes — SPEC-PORTAL-KENNIS-001.

Endpoints feeding the "alles is een bron" UI on the portal:

  GET  /knowledge/v1/kb/{kb_slug}/sources?org_id=...
       → Aggregate list: connectors (one row per source_connector_id with
         items_count + chunks_count) plus uploads (one row per direct
         artifact). Portal-api enriches connectors with display metadata
         from its own connectors table.

  GET  /knowledge/v1/kb/{kb_slug}/connectors/{connector_id}/items
       ?org_id=...&limit=...&offset=...
       → Paginated list of artifacts under one connector.

  GET  /knowledge/v1/kb/uploads/{artifact_id}/chunks
       ?org_id=...&limit=...&offset=...
       → Paginated list of parent_chunks for one direct-upload artifact.

  POST /knowledge/v1/kb/chunks-summary?org_id=...
       body: {"kb_slugs": [...]}
       → ``{slug: chunk_count}`` for the requested KBs in one call.
       Used by portal-api stats-summary to fill the per-KB "M chunks"
       count without an N+1 fan-out.

All endpoints assert tenant identity via ``assert_caller_identity_tenant_only``
(SPEC-TI-003) and use ``tenant_scoped_connection`` so RLS context is set on
the connection before the SELECTs.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from knowledge_ingest import pg_store
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.identity import assert_caller_identity_tenant_only

logger = structlog.get_logger()
router = APIRouter()


# -- Response models --------------------------------------------------------


class ConnectorAggregate(BaseModel):
    connector_id: str
    items_count: int = 0
    chunks_count: int = 0


class UploadSummary(BaseModel):
    id: str
    path: str
    content_type: str
    created_at: int
    chunks_count: int = 0


class KBSourcesResponse(BaseModel):
    connectors: list[ConnectorAggregate] = Field(default_factory=list)
    uploads: list[UploadSummary] = Field(default_factory=list)


class ItemSummary(BaseModel):
    id: str
    path: str
    content_type: str
    created_at: int
    chunks_count: int = 0


class ConnectorItemsResponse(BaseModel):
    items: list[ItemSummary] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class ChunkSummary(BaseModel):
    id: int
    position: int
    text: str
    token_count: int


class ArtifactChunksResponse(BaseModel):
    chunks: list[ChunkSummary] = Field(default_factory=list)
    total: int = 0
    limit: int
    offset: int


class ChunksSummaryRequest(BaseModel):
    kb_slugs: list[str] = Field(default_factory=list)


class ChunksSummaryResponse(BaseModel):
    chunks_by_kb: dict[str, int] = Field(default_factory=dict)


# -- Endpoints --------------------------------------------------------------


@router.get("/knowledge/v1/kb/{kb_slug}/sources", response_model=KBSourcesResponse)
async def get_kb_sources(
    request: Request,
    kb_slug: str,
    org_id: str = Query(..., description="Zitadel org ID"),
) -> KBSourcesResponse:
    """List bronnen for a KB: connector aggregates + direct uploads."""
    verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    async with tenant_scoped_connection(verified_org_id) as conn:
        result = await pg_store.list_kb_sources(conn, verified_org_id, kb_slug)
    return KBSourcesResponse(
        connectors=[ConnectorAggregate(**row) for row in result["connectors"]],
        uploads=[UploadSummary(**row) for row in result["uploads"]],
    )


@router.get(
    "/knowledge/v1/kb/{kb_slug}/connectors/{connector_id}/items",
    response_model=ConnectorItemsResponse,
)
async def list_connector_items(
    request: Request,
    kb_slug: str,
    connector_id: str,
    org_id: str = Query(..., description="Zitadel org ID"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ConnectorItemsResponse:
    """Drill-down: items under a connector with chunk counts."""
    verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    async with tenant_scoped_connection(verified_org_id) as conn:
        items, total = await pg_store.list_artifacts_for_connector(
            conn, verified_org_id, kb_slug, connector_id, limit, offset
        )
    return ConnectorItemsResponse(
        items=[ItemSummary(**row) for row in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/knowledge/v1/kb/uploads/{artifact_id}/chunks",
    response_model=ArtifactChunksResponse,
)
async def list_upload_chunks(
    request: Request,
    artifact_id: str,
    org_id: str = Query(..., description="Zitadel org ID"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ArtifactChunksResponse:
    """Drill-down: parent_chunks for a direct-upload artifact."""
    verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    async with tenant_scoped_connection(verified_org_id) as conn:
        chunks, total = await pg_store.list_chunks_for_artifact(
            conn, verified_org_id, artifact_id, limit, offset
        )
    return ArtifactChunksResponse(
        chunks=[ChunkSummary(**row) for row in chunks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/knowledge/v1/kb/chunks-summary",
    response_model=ChunksSummaryResponse,
)
async def chunks_summary(
    request: Request,
    body: ChunksSummaryRequest,
    org_id: str = Query(..., description="Zitadel org ID"),
) -> ChunksSummaryResponse:
    """Bulk chunk counts per KB for the portal stats-summary endpoint."""
    if len(body.kb_slugs) > 200:
        raise HTTPException(status_code=400, detail="kb_slugs limited to 200 per call")
    verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    if not body.kb_slugs:
        return ChunksSummaryResponse(chunks_by_kb={})
    async with tenant_scoped_connection(verified_org_id) as conn:
        chunks_by_kb = await pg_store.count_chunks_per_kb(conn, verified_org_id, body.kb_slugs)
    return ChunksSummaryResponse(chunks_by_kb=chunks_by_kb)
