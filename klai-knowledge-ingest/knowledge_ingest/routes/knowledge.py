"""
Knowledge management routes:
  POST /knowledge/v1/crawl - enqueue a bulk web crawl job
"""
import json
import logging
import time
import uuid

from fastapi import APIRouter

from knowledge_ingest.db import get_pool
from knowledge_ingest.models import BulkCrawlRequest, BulkCrawlResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/knowledge/v1/crawl", response_model=BulkCrawlResponse)
async def start_crawl(req: BulkCrawlRequest) -> BulkCrawlResponse:
    """Enqueue a bulk web crawl job. Returns immediately with job ID."""
    job_id = str(uuid.uuid4())
    now = int(time.time())
    pool = await get_pool()

    await pool.execute(
        """INSERT INTO knowledge.crawl_jobs
           (id, org_id, kb_slug, config, status, created_at, updated_at)
           VALUES ($1, $2, $3, $4, 'pending', $5, $5)""",
        job_id, req.org_id, req.kb_slug,
        json.dumps(req.model_dump()), now,
    )

    from knowledge_ingest import enrichment_tasks  # noqa: PLC0415
    proc_app = enrichment_tasks.get_app()
    await proc_app.run_crawl.defer_async(  # type: ignore[attr-defined]
        job_id=job_id,
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        start_url=req.start_url,
        max_depth=req.max_depth,
        include_patterns=req.include_patterns,
        exclude_patterns=req.exclude_patterns,
        rate_limit=req.rate_limit,
        content_selector=req.content_selector,
    )

    logger.info(
        "Enqueued crawl job %s for %s/%s starting at %s",
        job_id, req.org_id, req.kb_slug, req.start_url,
    )
    return BulkCrawlResponse(job_id=job_id, status="pending")
