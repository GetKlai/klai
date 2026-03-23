"""
GET /api/knowledge/stats

Returns the number of indexed chunks (Qdrant vectors) for the current org,
split by personal and org scope.
"""

import asyncio
import base64
import json
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


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without signature verification.

    Safe to use after the token has already been validated via get_userinfo.
    """
    try:
        payload_b64 = token.split(".")[1]
        # Add padding if needed
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


async def _qdrant_count(filters: dict) -> int:
    """Count points in the klai_knowledge collection matching the given payload filter."""
    try:
        headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.qdrant_url}/collections/{QDRANT_COLLECTION}/points/count",
                headers=headers,
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

    # Zitadel does not include resourceowner:id in JWT access tokens or userinfo.
    # It IS available via introspection, but the portal app has no introspect credentials.
    # Fallback: Management API get_user_by_id returns details.resourceOwner (PAT-authenticated).
    jwt_claims = _decode_jwt_payload(credentials.credentials)
    org_id = jwt_claims.get("urn:zitadel:iam:user:resourceowner:id") or info.get(
        "urn:zitadel:iam:user:resourceowner:id"
    )
    if not org_id:
        user_id = info.get("sub", "")
        if user_id:
            try:
                user_data = await zitadel.get_user_by_id(user_id)
                org_id = user_data.get("user", {}).get("details", {}).get("resourceOwner")
            except Exception as mgmt_exc:
                logger.warning("Could not fetch user org via Management API: %s", mgmt_exc)
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
