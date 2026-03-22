"""
PostgreSQL integration — stub for Phase 2.

The knowledge.artifacts table is for the semantic graph layer (Phase 4+).
Document-level tracking in Phase 2 uses Qdrant payload as source of truth.
This module is wired in but not called in the critical ingest/retrieve path.
"""
import logging

logger = logging.getLogger(__name__)


async def record_ingest(org_id: str, kb_slug: str, path: str, chunk_count: int) -> None:
    """Log ingest — PostgreSQL tracking added in Phase 4."""
    logger.debug("Indexed %s/%s for org %s (%d chunks)", kb_slug, path, org_id, chunk_count)
