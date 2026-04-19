"""
Taxonomy endpoints -- SPEC-KB-022 R9, bootstrap, coverage stats.

POST /ingest/v1/taxonomy/backfill
- Enqueues a Procrastinate background job for 3-phase backfill
- Returns immediately with job_id and status "queued"
- Deduplicates: same (org_id, kb_slug) returns existing job_id

GET /ingest/v1/taxonomy/backfill/{job_id}
- Returns job status (queued/doing/succeeded/failed) and result when done

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


class BackfillEnqueueResponse(BaseModel):
    job_id: int
    status: str


class BackfillStatusResponse(BaseModel):
    job_id: int
    status: str
    result: dict | None = None


class BootstrapRequest(BaseModel):
    org_id: str
    kb_slug: str
    batch_size: int = 500  # how many chunks to scan for document summaries


class BootstrapResponse(BaseModel):
    documents_scanned: int
    proposals_submitted: int


def _verify_internal_token(request: Request) -> None:
    """Verify X-Internal-Token header.

    SEC-014: fail-closed. Empty/missing PORTAL_INTERNAL_TOKEN is caught at
    startup by the pydantic validator in knowledge_ingest.config — here the
    only failure mode is a wrong or absent header, which always returns 401.
    """
    import hmac

    token = request.headers.get("x-internal-token", "")
    if not token or not hmac.compare_digest(token, settings.portal_internal_token):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------------------------------------------------------------------------
# Procrastinate job status mapping
# ---------------------------------------------------------------------------
# Procrastinate stores status as text in procrastinate_jobs.status.
# Possible values: todo, doing, succeeded, failed, cancelled, aborting.
# We map to a simpler vocabulary for the API consumer.
_STATUS_MAP = {
    "todo": "queued",
    "doing": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "failed",
    "aborting": "running",
}


@router.post("/ingest/v1/taxonomy/backfill", response_model=BackfillEnqueueResponse)
async def taxonomy_backfill(request: Request, req: BackfillRequest) -> BackfillEnqueueResponse:
    """Enqueue a taxonomy backfill job for the given KB.

    The 3-phase backfill (migrate, classify, tag) runs as a Procrastinate
    background task. Returns immediately with the job_id.

    Deduplication: if a backfill for the same (org_id, kb_slug) is already
    queued or running, the existing job_id is returned instead of enqueuing
    a duplicate.
    """
    from knowledge_ingest.enrichment_tasks import get_app

    proc_app = get_app()
    lock = f"taxonomy-backfill:{req.org_id}:{req.kb_slug}"

    # Check for an existing queued/running job with the same queueing_lock.
    # Procrastinate prevents duplicate queueing_lock values for todo/doing jobs,
    # but we query first to return the existing job_id to the caller.
    from knowledge_ingest.db import get_pool

    pool = await get_pool()
    existing = await pool.fetchrow(
        """
        SELECT id, status
        FROM procrastinate_jobs
        WHERE queueing_lock = $1
          AND status IN ('todo', 'doing')
        ORDER BY id DESC
        LIMIT 1
        """,
        lock,
    )
    if existing:
        logger.info(
            "taxonomy_backfill_dedup",
            org_id=req.org_id,
            kb_slug=req.kb_slug,
            existing_job_id=existing["id"],
            existing_status=existing["status"],
        )
        return BackfillEnqueueResponse(
            job_id=existing["id"],
            status=_STATUS_MAP.get(existing["status"], existing["status"]),
        )

    # Enqueue a new backfill job
    job_id = await proc_app.run_taxonomy_backfill.configure(
        queueing_lock=lock,
    ).defer_async(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        batch_size=req.batch_size,
    )

    logger.info(
        "taxonomy_backfill_enqueued",
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        job_id=job_id,
    )
    return BackfillEnqueueResponse(job_id=job_id, status="queued")


@router.get("/ingest/v1/taxonomy/backfill/{job_id}", response_model=BackfillStatusResponse)
async def taxonomy_backfill_status(request: Request, job_id: int) -> BackfillStatusResponse:
    """Check the status of a taxonomy backfill job.

    Returns the Procrastinate job status and, when the job has succeeded,
    the result dict with migrated/classified/tagged/skipped counts.
    """
    from knowledge_ingest.db import get_pool

    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, status, task_name
        FROM procrastinate_jobs
        WHERE id = $1
        """,
        job_id,
    )

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    if row["task_name"] != "run_taxonomy_backfill":
        raise HTTPException(status_code=404, detail="Job not found")

    status = _STATUS_MAP.get(row["status"], row["status"])

    # Procrastinate stores task return values in procrastinate_events
    result = None
    if row["status"] == "succeeded":
        event_row = await pool.fetchrow(
            """
            SELECT result
            FROM procrastinate_events
            WHERE job_id = $1
              AND type = 'succeeded'
            ORDER BY id DESC
            LIMIT 1
            """,
            job_id,
        )
        if event_row and event_row["result"]:
            import json

            raw = event_row["result"]
            result = json.loads(raw) if isinstance(raw, str) else raw

    return BackfillStatusResponse(job_id=job_id, status=status, result=result)


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

    # Fetch existing category names so the LLM doesn't propose duplicates.
    existing_nodes = await fetch_taxonomy_nodes(req.kb_slug, req.org_id)
    existing_names = [n.name for n in existing_nodes]

    proposals_submitted = await generate_bootstrap_proposals(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        documents=documents,
        existing_category_names=existing_names,
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


# ---------------------------------------------------------------------------
# Classify endpoint (SPEC-KB-026 R4 part 1)
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    org_id: str
    kb_slug: str
    text: str


class ClassifyResponse(BaseModel):
    taxonomy_node_ids: list[int]


@router.post("/ingest/v1/taxonomy/classify", response_model=ClassifyResponse)
async def taxonomy_classify(request: Request, req: ClassifyRequest) -> ClassifyResponse:
    """Classify a text query against a KB's taxonomy nodes.

    Used by the portal to classify gap events. Returns empty list when
    no taxonomy nodes exist or classification yields no matches.
    """
    nodes = await fetch_taxonomy_nodes(req.kb_slug, req.org_id)
    if not nodes:
        return ClassifyResponse(taxonomy_node_ids=[])

    matched_nodes, _tags = await classify_document(
        title="",
        content_preview=req.text,
        taxonomy_nodes=nodes,
    )
    node_ids = [nid for nid, _conf in matched_nodes]
    return ClassifyResponse(taxonomy_node_ids=node_ids)


# ---------------------------------------------------------------------------
# Auto-categorise job endpoint (SPEC-KB-026 R5)
# ---------------------------------------------------------------------------


class AutoCategoriseJobRequest(BaseModel):
    org_id: str
    kb_slug: str
    node_id: int
    cluster_centroid: list[float] | None = None


class AutoCategoriseJobResponse(BaseModel):
    job_id: int
    status: str


@router.post(
    "/ingest/v1/taxonomy/auto-categorise-job",
    response_model=AutoCategoriseJobResponse,
    status_code=202,
)
async def taxonomy_auto_categorise_job(
    request: Request,
    req: AutoCategoriseJobRequest,
) -> AutoCategoriseJobResponse:
    """Enqueue an auto-categorise background job via Procrastinate.

    Called by the portal when a taxonomy proposal is approved.
    Returns immediately with job_id; the actual work runs in the background
    with retries (max 3, exponential backoff).
    """
    from knowledge_ingest.enrichment_tasks import get_app

    proc_app = get_app()
    job_id = await proc_app.run_auto_categorise.defer_async(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        node_id=req.node_id,
        cluster_centroid=req.cluster_centroid,
    )

    logger.info(
        "auto_categorise_job_enqueued",
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        node_id=req.node_id,
        job_id=job_id,
    )
    return AutoCategoriseJobResponse(job_id=job_id, status="queued")


class CoverageNodeStats(BaseModel):
    taxonomy_node_id: int
    chunk_count: int


class CoverageStatsResponse(BaseModel):
    nodes: list[CoverageNodeStats]
    total_chunks: int
    untagged_count: int


class TagEntry(BaseModel):
    tag: str
    count: int


class TopTagsResponse(BaseModel):
    tags: list[TagEntry]
    total_chunks_sampled: int


@router.get("/ingest/v1/taxonomy/top-tags", response_model=TopTagsResponse)
async def taxonomy_top_tags(
    request: Request,
    kb_slug: str,
    org_id: str,
    limit: int = 20,
    taxonomy_node_id: int | None = None,
) -> TopTagsResponse:
    """Return top N tags by frequency across KB chunks.

    Scrolls up to 2000 chunks (sampled) to count tag occurrences.
    Optionally filters by taxonomy_node_id to get tags within a category.
    """
    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    must_conditions = [
        FieldCondition(key="org_id", match=MatchValue(value=org_id)),
        FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
    ]
    if taxonomy_node_id is not None:
        from qdrant_client.models import MatchAny

        must_conditions.append(
            FieldCondition(
                key="taxonomy_node_ids",
                match=MatchAny(any=[taxonomy_node_id]),
            )
        )

    scroll_filter = Filter(must=must_conditions)

    tag_counts: dict[str, int] = {}
    total_sampled = 0
    offset = None
    max_scroll = 2000

    while total_sampled < max_scroll:
        batch_size = min(100, max_scroll - total_sampled)
        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=scroll_filter,
                limit=batch_size,
                offset=offset,
                with_payload=["tags"],
                with_vectors=False,
            ),
            timeout=20.0,
        )
        if not points:
            break

        for point in points:
            payload = point.payload or {}
            for tag in payload.get("tags") or []:
                if isinstance(tag, str) and tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
            total_sampled += 1

        if next_offset is None:
            break
        offset = next_offset

    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return TopTagsResponse(
        tags=[TagEntry(tag=t, count=c) for t, c in sorted_tags],
        total_chunks_sampled=total_sampled,
    )


# ---------------------------------------------------------------------------
# Auto-categorise endpoint (SPEC-KB-024 R4)
# ---------------------------------------------------------------------------


class AutoCategoriseRequest(BaseModel):
    org_id: str
    kb_slug: str
    node_id: int
    cluster_centroid: list[float]


class AutoCategoriseResponse(BaseModel):
    categorised: int


async def _auto_categorise_impl(
    org_id: str,
    kb_slug: str,
    node_id: int,
    cluster_centroid: list[float],
    threshold: float,
) -> int:
    """Bulk assign taxonomy_node_id to existing documents matching a cluster centroid.

    Pure cosine similarity -- no LLM calls. Returns count of categorised documents.
    Two-pass approach: first pass identifies matching artifact_ids via centroid similarity,
    second pass tags ALL chunks of matching documents (not just the first chunk).
    """
    from knowledge_ingest.clustering import cosine_similarity

    client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    scroll_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
        ]
    )

    # Pass 1: identify matching artifact_ids via centroid similarity (dedup to first chunk)
    matched_artifacts: set[str] = set()
    seen_artifacts: set[str] = set()
    offset = None

    while True:
        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=scroll_filter,
                limit=100,
                offset=offset,
                with_payload=["artifact_id", "taxonomy_node_ids"],
                with_vectors=["vector_chunk"],
            ),
            timeout=60.0,
        )

        if not points:
            break

        for point in points:
            payload = point.payload or {}
            artifact_id = payload.get("artifact_id") or str(point.id)
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)

            vec = None
            if hasattr(point, "vector") and point.vector:
                if isinstance(point.vector, dict):
                    vec = point.vector.get("vector_chunk")
                elif isinstance(point.vector, list):
                    vec = point.vector
            if vec is None:
                continue

            sim = cosine_similarity(vec, cluster_centroid)
            if sim >= threshold:
                matched_artifacts.add(artifact_id)

        if next_offset is None:
            break
        offset = next_offset

    if not matched_artifacts:
        logger.info(
            "auto_categorise_no_matches",
            org_id=org_id,
            kb_slug=kb_slug,
            node_id=node_id,
        )
        return 0

    # Pass 2: tag ALL chunks of matching documents
    categorised = 0
    offset = None

    while True:
        points, next_offset = await asyncio.wait_for(
            client.scroll(
                collection_name=COLLECTION,
                scroll_filter=scroll_filter,
                limit=100,
                offset=offset,
                with_payload=["artifact_id", "taxonomy_node_ids"],
                with_vectors=False,
            ),
            timeout=60.0,
        )

        if not points:
            break

        points_to_update: list[tuple[str | int, list[int]]] = []
        for point in points:
            payload = point.payload or {}
            artifact_id = payload.get("artifact_id") or str(point.id)
            if artifact_id not in matched_artifacts:
                continue
            current_ids = payload.get("taxonomy_node_ids") or []
            if node_id not in current_ids:
                new_ids = list({*current_ids, node_id})
                points_to_update.append((point.id, new_ids))

        for point_id, new_ids in points_to_update:
            await asyncio.wait_for(
                client.set_payload(
                    collection_name=COLLECTION,
                    payload={"taxonomy_node_ids": new_ids},
                    points=[point_id],
                ),
                timeout=10.0,
            )
            categorised += 1

        if next_offset is None:
            break
        offset = next_offset

    logger.info(
        "auto_categorise_complete",
        org_id=org_id,
        kb_slug=kb_slug,
        node_id=node_id,
        categorised_chunks=categorised,
        matched_documents=len(matched_artifacts),
    )
    return len(matched_artifacts)


@router.post("/ingest/v1/taxonomy/auto-categorise", response_model=AutoCategoriseResponse)
async def taxonomy_auto_categorise(
    request: Request,
    req: AutoCategoriseRequest,
) -> AutoCategoriseResponse:
    """Bulk assign taxonomy_node_id to existing documents matching a cluster centroid.

    Called when a taxonomy proposal is approved in the portal.
    No LLM calls -- pure cosine similarity against provided centroid (SPEC-KB-024 R4).
    """
    _verify_internal_token(request)
    categorised = await _auto_categorise_impl(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        node_id=req.node_id,
        cluster_centroid=req.cluster_centroid,
        threshold=settings.taxonomy_auto_categorise_threshold,
    )
    return AutoCategoriseResponse(categorised=categorised)


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
        node_stats.append(
            CoverageNodeStats(
                taxonomy_node_id=node.id,
                chunk_count=count_result.count,
            )
        )

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
