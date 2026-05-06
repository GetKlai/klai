"""
Knowledge management routes:
  POST /knowledge/v1/crawl - enqueue a bulk web crawl job
"""
import json
import time
import uuid

import structlog
from fastapi import APIRouter, Request

from knowledge_ingest.db import get_pool, tenant_scoped_connection
from knowledge_ingest.identity import assert_caller_identity
from knowledge_ingest.models import BulkCrawlRequest, BulkCrawlResponse

logger = structlog.get_logger()
router = APIRouter()


@router.post("/knowledge/v1/crawl", response_model=BulkCrawlResponse)
async def start_crawl(req: BulkCrawlRequest, request: Request) -> BulkCrawlResponse:
    """Enqueue a bulk web crawl job. Returns immediately with job ID.

    SPEC-TI-003 AC-6: identity assertion replaces body-trust on org_id.
    """
    # AC-6: verify caller identity before trusting req.org_id
    verified_org_id = await assert_caller_identity(
        request, claimed_org_id=req.org_id, claimed_user_id=None
    )

    job_id = str(uuid.uuid4())
    now = int(time.time())

    # AC-9: use tenant_scoped_connection so RLS context is set for the INSERT
    async with tenant_scoped_connection(verified_org_id) as conn:
        await conn.execute(
            """INSERT INTO knowledge.crawl_jobs
               (id, org_id, kb_slug, config, status, created_at, updated_at)
               VALUES ($1, $2, $3, $4, 'pending', $5, $5)""",
            job_id, verified_org_id, req.kb_slug,
            json.dumps(req.model_dump()), now,
        )

    from knowledge_ingest import enrichment_tasks
    proc_app = enrichment_tasks.get_app()
    await proc_app.run_crawl.defer_async(  # type: ignore[attr-defined]
        job_id=job_id,
        org_id=verified_org_id,
        kb_slug=req.kb_slug,
        start_url=req.start_url,
        max_depth=req.max_depth,
        max_pages=req.max_pages,
        include_patterns=req.include_patterns,
        exclude_patterns=req.exclude_patterns,
        rate_limit=req.rate_limit,
        content_selector=req.content_selector,
    )

    logger.info(
        "enqueued_crawl_job",
        job_id=job_id,
        org_id=verified_org_id,
        kb_slug=req.kb_slug,
        start_url=req.start_url,
    )
    return BulkCrawlResponse(job_id=job_id, status="pending")
