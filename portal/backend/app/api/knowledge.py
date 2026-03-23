"""
GET /api/knowledge/stats

Returns the number of indexed chunks (Qdrant vectors) for the current org,
split by personal and org scope.
"""

import asyncio
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.config import settings
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
bearer = HTTPBearer()

# The single shared Qdrant collection used by knowledge-ingest
QDRANT_COLLECTION = "klai_knowledge"


class KnowledgeStats(BaseModel):
    personal_count: int
    org_count: int


async def _qdrant_count(filters: dict) -> int:
    """Count points in the klai_knowledge collection matching the given payload filter."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.qdrant_url}/collections/{QDRANT_COLLECTION}/points/count",
                json={"filter": filters, "exact": True},
            )
        if resp.status_code == 404:
            # Collection does not exist yet
            return 0
        if not resp.is_success:
            logger.warning("Qdrant count request failed: HTTP %s", resp.status_code)
            return 0
        return resp.json().get("result", {}).get("count", 0) or 0
    except Exception as exc:
        logger.warning("Could not reach Qdrant for knowledge count: %s", exc)
        return 0


@router.get("/stats", response_model=KnowledgeStats)
async def get_knowledge_stats(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> KnowledgeStats:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Ongeldig of verlopen token",
        ) from exc

    org_id = info.get("urn:zitadel:iam:user:resourceowner:id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Geen organisatie gevonden voor deze gebruiker",
        )

    user_id = info.get("sub", "")

    # Count org and personal chunks concurrently — independent Qdrant queries.
    org_filter = {
        "must": [
            {"key": "org_id", "match": {"value": org_id}},
            {"key": "kb_slug", "match": {"value": "org"}},
        ],
    }
    personal_filter = (
        {
            "must": [
                {"key": "org_id", "match": {"value": org_id}},
                {"key": "kb_slug", "match": {"value": "personal"}},
                {"key": "user_id", "match": {"value": user_id}},
            ],
        }
        if user_id
        else None
    )

    if personal_filter is not None:
        org_count, personal_count = await asyncio.gather(
            _qdrant_count(org_filter),
            _qdrant_count(personal_filter),
        )
    else:
        org_count = await _qdrant_count(org_filter)
        personal_count = 0

    return KnowledgeStats(personal_count=personal_count, org_count=org_count)
