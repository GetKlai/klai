"""KB sources routes — SPEC-PORTAL-KENNIS-001 / SPEC-PORTAL-KENNIS-002.

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

  POST /knowledge/v1/artifacts/{artifact_id}/reindex?org_id=...
       → Set index_status="pending" and re-enqueue enrichment (SPEC-PORTAL-KENNIS-002 A2).

  DELETE /knowledge/v1/kb/{kb_slug}/uploads/{artifact_id}?org_id=...&user_id=...
       → Soft-delete a direct-upload artifact + remove Qdrant chunks (B2).

All endpoints assert tenant identity via ``assert_caller_identity_tenant_only``
(SPEC-TI-003) and use ``tenant_scoped_connection`` so RLS context is set on
the connection before the SELECTs.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from knowledge_ingest import enrichment_tasks, pg_store, qdrant_store
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
    index_status: str = "synced"


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
    bronnen_by_kb: dict[str, int] = Field(default_factory=dict)


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
        return ChunksSummaryResponse(chunks_by_kb={}, bronnen_by_kb={})
    async with tenant_scoped_connection(verified_org_id) as conn:
        chunks_by_kb = await pg_store.count_chunks_per_kb(conn, verified_org_id, body.kb_slugs)
        bronnen_by_kb = await pg_store.count_sources_per_kb(conn, verified_org_id, body.kb_slugs)
    return ChunksSummaryResponse(chunks_by_kb=chunks_by_kb, bronnen_by_kb=bronnen_by_kb)


# -- SPEC-PORTAL-KENNIS-002: reindex + delete ----------------------------------


class ReindexResponse(BaseModel):
    artifact_id: str
    index_status: str


@router.post(
    "/knowledge/v1/artifacts/{artifact_id}/reindex",
    response_model=ReindexResponse,
    status_code=202,
)
async def reindex_upload(
    request: Request,
    artifact_id: str,
    org_id: str = Query(..., description="Zitadel org ID"),
) -> ReindexResponse:
    """Set index_status='pending' and re-enqueue enrichment for a direct-upload artifact.

    Returns 404 when the artifact does not exist or belongs to a different org.
    Returns 202 Accepted when the enrichment job has been enqueued.
    """
    verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    async with tenant_scoped_connection(verified_org_id) as conn:
        updated = await pg_store.set_artifact_index_status(
            conn, artifact_id, verified_org_id, "pending"
        )
    if updated is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    proc_app = enrichment_tasks.get_app()
    await proc_app.enrich_document_interactive.configure(
        queueing_lock=f"reindex:{verified_org_id}:{artifact_id}",
    ).defer_async(artifact_id=artifact_id)

    logger.info(
        "reindex_enqueued",
        artifact_id=artifact_id,
        org_id=verified_org_id,
    )
    return ReindexResponse(artifact_id=artifact_id, index_status="pending")


@router.delete(
    "/knowledge/v1/kb/{kb_slug}/uploads/{artifact_id}",
    status_code=204,
)
async def delete_upload(
    request: Request,
    kb_slug: str,
    artifact_id: str,
    org_id: str = Query(..., description="Zitadel org ID"),
    user_id: str | None = Query(
        default=None,
        description=(
            "When provided, the artifact is deleted only if it belongs to this user "
            "(contributor-scoped delete). Omit for owner/admin deletes."
        ),
    ),
) -> None:
    """Soft-delete a direct-upload artifact and remove its Qdrant chunks.

    Returns 404 when the artifact does not exist.
    Returns 403 when ``user_id`` is provided but does not match the artifact owner.
    """
    verified_org_id = await assert_caller_identity_tenant_only(request, claimed_org_id=org_id)
    async with tenant_scoped_connection(verified_org_id) as conn:
        artifact = await pg_store.get_kb_upload_artifact(
            conn, artifact_id, verified_org_id, kb_slug
        )
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")

        # Contributor ownership check: only allow deleting own uploads.
        if user_id is not None and artifact["user_id"] != user_id:
            raise HTTPException(
                status_code=403,
                detail="you can only delete your own uploads",
            )

        await pg_store.soft_delete_artifact(conn, verified_org_id, kb_slug, artifact["path"])

    await qdrant_store.delete_document(verified_org_id, kb_slug, artifact["path"])
    logger.info(
        "upload_deleted",
        artifact_id=artifact_id,
        kb_slug=kb_slug,
        org_id=verified_org_id,
    )
