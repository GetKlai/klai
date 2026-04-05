"""
Taxonomy backfill endpoint — SPEC-KB-021 R5.

POST /ingest/v1/taxonomy/backfill
- Protected by X-Internal-Token header
- Scrolls Qdrant for chunks without taxonomy_node_id payload
- Classifies each unique document once, updates all its chunks via set_payload
- Returns { "processed": int, "tagged": int, "skipped": int }
- Idempotent: re-running on already-tagged chunks is a no-op
"""
from __future__ import annotations

import asyncio
import warnings

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")

from qdrant_client import AsyncQdrantClient  # noqa: E402
from qdrant_client.models import FieldCondition, Filter, IsNullCondition, MatchValue  # noqa: E402

from knowledge_ingest.config import settings  # noqa: E402
from knowledge_ingest.portal_client import fetch_taxonomy_nodes  # noqa: E402
from knowledge_ingest.proposal_generator import DocumentSummary, generate_bootstrap_proposals  # noqa: E402
from knowledge_ingest.taxonomy_classifier import classify_document  # noqa: E402

logger = structlog.get_logger()
router = APIRouter()

COLLECTION = "klai_knowledge"


class BackfillRequest(BaseModel):
    org_id: str
    kb_slug: str
    batch_size: int = 100


class BackfillResponse(BaseModel):
    processed: int
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
    import hmac  # noqa: PLC0415
    if not token or not hmac.compare_digest(token, settings.portal_internal_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/ingest/v1/taxonomy/backfill", response_model=BackfillResponse)
async def taxonomy_backfill(request: Request, req: BackfillRequest) -> BackfillResponse:
    """Backfill taxonomy_node_id for existing chunks that predate this feature."""
    _verify_internal_token(request)

    taxonomy_nodes = await fetch_taxonomy_nodes(req.kb_slug, req.org_id)
    if not taxonomy_nodes:
        logger.info(
            "taxonomy_backfill_no_nodes",
            kb_slug=req.kb_slug,
            org_id=req.org_id,
        )
        return BackfillResponse(processed=0, tagged=0, skipped=0)

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    # Filter: chunks for this org/KB that have no taxonomy_node_id field
    # IsNullCondition matches points where the field is absent (not set)
    scroll_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=req.org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=req.kb_slug)),
            IsNullCondition(key="taxonomy_node_id", is_null=True),
        ]
    )

    processed = 0
    tagged = 0
    skipped = 0
    offset = None

    while True:
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

        # Group points by (path, artifact_id) so we classify each document once
        doc_groups: dict[str, list] = {}
        for point in points:
            payload = point.payload or {}
            doc_key = payload.get("artifact_id") or payload.get("path") or str(point.id)
            if doc_key not in doc_groups:
                doc_groups[doc_key] = []
            doc_groups[doc_key].append(point)

        # Classify each unique document and update all its chunks
        for doc_key, doc_points in doc_groups.items():
            # Extract title + content preview from the first chunk
            first_payload = doc_points[0].payload or {}
            title = first_payload.get("title") or first_payload.get("path") or doc_key
            content_preview = doc_points[0].payload.get("text", "")[:500] if doc_points[0].payload else ""

            node_id, _confidence = await classify_document(
                title=title,
                content_preview=content_preview,
                taxonomy_nodes=taxonomy_nodes,
            )

            point_ids = [p.id for p in doc_points]
            await client.set_payload(
                COLLECTION,
                payload={"taxonomy_node_id": node_id},
                points=point_ids,
            )

            processed += len(point_ids)
            if node_id is not None:
                tagged += len(point_ids)

        if next_offset is None:
            break
        offset = next_offset

    logger.info(
        "taxonomy_backfill_complete",
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        processed=processed,
        tagged=tagged,
        skipped=skipped,
    )
    return BackfillResponse(processed=processed, tagged=tagged, skipped=skipped)


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
    _verify_internal_token(request)

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
