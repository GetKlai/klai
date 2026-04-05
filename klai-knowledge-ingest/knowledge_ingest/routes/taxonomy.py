"""
Taxonomy endpoints -- SPEC-KB-022 R9, bootstrap, coverage stats.

POST /ingest/v1/taxonomy/backfill
- Three phases: (1) migrate taxonomy_node_id -> taxonomy_node_ids, (2) re-classify, (3) tag
- Idempotent: chunks with existing taxonomy_node_ids are skipped

POST /ingest/v1/taxonomy/bootstrap-proposals
- Scan existing chunks, generate bootstrap proposals with descriptions

GET /ingest/v1/taxonomy/coverage-stats
- Query Qdrant counts per taxonomy node for the coverage dashboard
"""
from __future__ import annotations

import asyncio
import warnings

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

from qdrant_client import AsyncQdrantClient  # noqa: E402
from qdrant_client.models import (  # noqa: E402
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchValue,
    PayloadField,
)

from knowledge_ingest.config import settings  # noqa: E402
from knowledge_ingest.portal_client import fetch_taxonomy_nodes  # noqa: E402
from knowledge_ingest.proposal_generator import (  # noqa: E402
    DocumentSummary,
    generate_bootstrap_proposals,
)
from knowledge_ingest.taxonomy_classifier import classify_document  # noqa: E402

logger = structlog.get_logger()
router = APIRouter()

COLLECTION = "klai_knowledge"


class BackfillRequest(BaseModel):
    org_id: str
    kb_slug: str
    batch_size: int = 100


class BackfillResponse(BaseModel):
    migrated: int
    classified: int
    tagged: int
    skipped: int


class BootstrapRequest(BaseModel):
    org_id: str
    kb_slug: str
    batch_size: int = 500  # how many chunks to scan for document summaries


class BootstrapResponse(BaseModel):
    documents_scanned: int
    proposals_submitted: int


def _verify_internal_token(request: Request) -> None:
    """Verify X-Internal-Token header."""
    if not settings.portal_internal_token:
        return
    token = request.headers.get("x-internal-token", "")
    import hmac
    if not token or not hmac.compare_digest(token, settings.portal_internal_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/ingest/v1/taxonomy/backfill", response_model=BackfillResponse)
async def taxonomy_backfill(request: Request, req: BackfillRequest) -> BackfillResponse:
    """Backfill multi-label taxonomy for existing chunks.

    Three phases:
    1. Migrate old taxonomy_node_id (int) -> taxonomy_node_ids: [old_value]
    2. Re-classify unclassified chunks with multi-label classifier
    3. Generate tags for all processed chunks
    Idempotent: chunks with existing taxonomy_node_ids are skipped.
    """
    taxonomy_nodes = await fetch_taxonomy_nodes(req.kb_slug, req.org_id)
    if not taxonomy_nodes:
        logger.info(
            "taxonomy_backfill_no_nodes",
            kb_slug=req.kb_slug,
            org_id=req.org_id,
        )
        return BackfillResponse(migrated=0, classified=0, tagged=0, skipped=0)

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    migrated = 0
    classified = 0
    tagged = 0
    skipped = 0

    # Phase 1: Migrate old taxonomy_node_id -> taxonomy_node_ids
    # Find chunks with old field that don't have new field
    offset = None
    while True:
        # Find chunks that have taxonomy_node_id but NOT taxonomy_node_ids
        # We scroll for chunks with taxonomy_node_id set (not null)
        # and taxonomy_node_ids absent (is_null)
        phase1_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=req.org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=req.kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
            ],
            must_not=[
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_id")),
            ],
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase1_filter,
                limit=req.batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        for point in points:
            payload = point.payload or {}
            old_id = payload.get("taxonomy_node_id")
            new_ids = [old_id] if old_id is not None else []
            await client.set_payload(
                COLLECTION,
                payload={"taxonomy_node_ids": new_ids},
                points=[point.id],
            )
            migrated += 1

        if next_offset is None:
            break
        offset = next_offset

    # Phase 2: Re-classify unclassified chunks (no taxonomy_node_id AND no taxonomy_node_ids)
    offset = None
    while True:
        phase2_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=req.org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=req.kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_id")),
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
            ]
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase2_filter,
                limit=req.batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        # Group by document
        doc_groups: dict[str, list] = {}
        for point in points:
            payload = point.payload or {}
            doc_key = payload.get("artifact_id") or payload.get("path") or str(point.id)
            if doc_key not in doc_groups:
                doc_groups[doc_key] = []
            doc_groups[doc_key].append(point)

        for doc_key, doc_points in doc_groups.items():
            first_payload = doc_points[0].payload or {}
            title = first_payload.get("title") or first_payload.get("path") or doc_key
            content_preview = first_payload.get("text", "")[:500]

            matched_nodes, suggested_tags = await classify_document(
                title=title,
                content_preview=content_preview,
                taxonomy_nodes=taxonomy_nodes,
            )
            node_ids = [nid for nid, _conf in matched_nodes]

            point_ids = [p.id for p in doc_points]
            update_payload: dict = {"taxonomy_node_ids": node_ids}
            if suggested_tags:
                update_payload["tags"] = suggested_tags
                tagged += len(point_ids)

            await client.set_payload(
                COLLECTION,
                payload=update_payload,
                points=point_ids,
            )
            classified += len(point_ids)

        if next_offset is None:
            break
        offset = next_offset

    # Phase 3: Generate tags for chunks that have taxonomy_node_ids but no tags
    offset = None
    while True:
        phase3_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=req.org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=req.kb_slug)),
                IsEmptyCondition(is_empty=PayloadField(key="tags")),
            ],
            must_not=[
                IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
            ],
        )

        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=phase3_filter,
                limit=req.batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        # Group by document for tag generation
        doc_groups_tag: dict[str, list] = {}
        for point in points:
            payload = point.payload or {}
            doc_key = payload.get("artifact_id") or payload.get("path") or str(point.id)
            if doc_key not in doc_groups_tag:
                doc_groups_tag[doc_key] = []
            doc_groups_tag[doc_key].append(point)

        for doc_key, doc_points in doc_groups_tag.items():
            first_payload = doc_points[0].payload or {}
            title = first_payload.get("title") or first_payload.get("path") or doc_key
            content_preview = first_payload.get("text", "")[:500]

            _, suggested_tags = await classify_document(
                title=title,
                content_preview=content_preview,
                taxonomy_nodes=taxonomy_nodes,
            )

            if suggested_tags:
                point_ids = [p.id for p in doc_points]
                await client.set_payload(
                    COLLECTION,
                    payload={"tags": suggested_tags},
                    points=point_ids,
                )
                tagged += len(point_ids)

        if next_offset is None:
            break
        offset = next_offset

    logger.info(
        "taxonomy_backfill_complete",
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        migrated=migrated,
        classified=classified,
        tagged=tagged,
        skipped=skipped,
    )
    return BackfillResponse(
        migrated=migrated, classified=classified, tagged=tagged, skipped=skipped,
    )


@router.post("/ingest/v1/taxonomy/bootstrap-proposals", response_model=BootstrapResponse)
async def taxonomy_bootstrap_proposals(
    request: Request, req: BootstrapRequest
) -> BootstrapResponse:
    """Scan existing Qdrant chunks and generate bootstrap taxonomy proposals.

    Reads existing chunks for this KB, groups by document, sends up to 50 document
    summaries to klai-fast which identifies 3-8 logical top-level categories,
    then submits one proposal per category to the portal review queue.

    Use this to bootstrap a KB taxonomy from scratch when no nodes exist yet.
    After accepting proposals in the portal, run /backfill to tag all chunks.
    """
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    scroll_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=req.org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=req.kb_slug)),
        ]
    )

    # Scroll chunks, one per artifact_id (we only need the first chunk per document)
    seen_artifacts: set[str] = set()
    documents: list[DocumentSummary] = []
    offset = None

    while len(documents) < 50:
        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=scroll_filter,
                limit=req.batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            ),
            timeout=30.0,
        )

        if not points:
            break

        for point in points:
            payload = point.payload or {}
            artifact_id = payload.get("artifact_id") or str(point.id)
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)
            title = payload.get("title") or payload.get("path") or artifact_id
            preview = payload.get("text", "")[:300]
            documents.append(DocumentSummary(title=title, content_preview=preview))
            if len(documents) >= 50:
                break

        if next_offset is None:
            break
        offset = next_offset

    if not documents:
        logger.info("bootstrap_proposals_no_documents", kb_slug=req.kb_slug, org_id=req.org_id)
        return BootstrapResponse(documents_scanned=0, proposals_submitted=0)

    proposals_submitted = await generate_bootstrap_proposals(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        documents=documents,
    )

    logger.info(
        "taxonomy_bootstrap_complete",
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        documents_scanned=len(documents),
        proposals_submitted=proposals_submitted,
    )
    return BootstrapResponse(
        documents_scanned=len(documents),
        proposals_submitted=proposals_submitted,
    )


class CoverageNodeStats(BaseModel):
    taxonomy_node_id: int
    chunk_count: int


class CoverageStatsResponse(BaseModel):
    nodes: list[CoverageNodeStats]
    total_chunks: int
    untagged_count: int


@router.get("/ingest/v1/taxonomy/coverage-stats", response_model=CoverageStatsResponse)
async def taxonomy_coverage_stats(
    request: Request,
    kb_slug: str,
    org_id: str,
) -> CoverageStatsResponse:
    """Query Qdrant for chunk counts per taxonomy node.

    Called by the portal to build the coverage dashboard.
    Returns per-node chunk counts + total and untagged counts.
    """

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    from qdrant_client.models import MatchAny

    # Get all taxonomy nodes from portal for this KB
    taxonomy_nodes = await fetch_taxonomy_nodes(kb_slug, org_id)

    # Count total chunks for this KB
    total_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
        ]
    )
    total_count = await asyncio.wait_for(
        client.count(collection_name=COLLECTION, count_filter=total_filter, exact=True),
        timeout=15.0,
    )
    total_chunks = total_count.count

    # Count chunks per taxonomy node
    node_stats: list[CoverageNodeStats] = []
    for node in taxonomy_nodes:
        node_filter = Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                FieldCondition(
                    key="taxonomy_node_ids",
                    match=MatchAny(any=[node.id]),
                ),
            ]
        )
        count_result = await asyncio.wait_for(
            client.count(collection_name=COLLECTION, count_filter=node_filter, exact=True),
            timeout=10.0,
        )
        node_stats.append(CoverageNodeStats(
            taxonomy_node_id=node.id,
            chunk_count=count_result.count,
        ))

    # Count untagged chunks (no taxonomy_node_ids field or empty)
    untagged_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
            IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
        ]
    )
    untagged_count_result = await asyncio.wait_for(
        client.count(collection_name=COLLECTION, count_filter=untagged_filter, exact=True),
        timeout=10.0,
    )

    return CoverageStatsResponse(
        nodes=node_stats,
        total_chunks=total_chunks,
        untagged_count=untagged_count_result.count,
    )
