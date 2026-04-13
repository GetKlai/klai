"""Partner knowledge ingest service.

SPEC-API-001 TASK-011:
- Proxy content to knowledge-ingest service for append-only ingestion.
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import Settings
from app.trace import get_trace_headers

logger = structlog.get_logger()


async def ingest_knowledge(
    org_id: int,
    zitadel_org_id: str,
    kb_slug: str,
    title: str | None,
    content: str,
    source_type: str,
    content_type: str,
    settings: Settings,
) -> dict:
    """Proxy to POST /ingest/v1/document on knowledge-ingest service.

    Returns {artifact_id, chunks_created, status} from ingest response.
    """
    ingest_url = settings.knowledge_ingest_url

    body = {
        "org_id": zitadel_org_id,
        "kb_slug": kb_slug,
        "path": title or "partner-upload",
        "content": content,
        "source_type": source_type,
        "content_type": content_type,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{ingest_url}/ingest/v1/document",
            json=body,
            headers={
                "X-Internal-Secret": settings.knowledge_ingest_secret,
                **get_trace_headers(),
            },
        )
        resp.raise_for_status()
        return resp.json()
