"""
Knowledge API routes:
  GET    /api/knowledge/stats                        — chunk counts by scope
  GET    /api/knowledge/personal/items               — list personal artifacts
  DELETE /api/knowledge/personal/items/{artifact_id}  — delete personal artifact
"""

import asyncio
import base64
import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.bearer import bearer
from app.api.dependencies import _get_caller_org, require_product
from app.core.config import settings
from app.core.database import get_db
from app.services.access import get_accessible_kb_slugs
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

# The single shared Qdrant collection used by knowledge-ingest
QDRANT_COLLECTION = "klai_knowledge"


class KnowledgeStats(BaseModel):
    personal_count: int
    org_count: int
    group_count: int


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


# @MX:NOTE fan_in=2 -- called for personal, org, and group slug counts
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


@router.get("/stats", response_model=KnowledgeStats, dependencies=[Depends(require_product("knowledge"))])
async def get_knowledge_stats(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeStats:
    try:
        info = await zitadel.get_userinfo(credentials.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
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
            detail="No organisation found for this user",
        )

    user_id = info.get("sub", "")

    # Resolve accessible kb_slugs (personal + org + group:{id} for each membership)
    accessible_slugs = await get_accessible_kb_slugs(user_id, db) if user_id else ["org"]
    group_slugs = [s for s in accessible_slugs if s.startswith("group:")]

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
                {"key": "kb_slug", "match": {"value": f"personal-{user_id}"}},
                {"key": "user_id", "match": {"value": user_id}},
            ],
        }
        if user_id
        else None
    )

    # Build group filters for all group-scoped KB slugs the user has access to
    group_filters = [
        {
            "must": [
                {"key": "org_id", "match": {"value": org_id}},
                {"key": "kb_slug", "match": {"value": slug}},
            ],
        }
        for slug in group_slugs
    ]

    tasks = [_qdrant_count(org_filter)]
    if personal_filter is not None:
        tasks.append(_qdrant_count(personal_filter))
    for gf in group_filters:
        tasks.append(_qdrant_count(gf))

    results = await asyncio.gather(*tasks)

    org_count = results[0]
    personal_count = results[1] if personal_filter is not None else 0
    group_count = sum(results[2:] if personal_filter is not None else results[1:])

    return KnowledgeStats(personal_count=personal_count, org_count=org_count, group_count=group_count)


# -- Personal knowledge items (proxy to knowledge-ingest) --------------------


@router.get("/personal/items", dependencies=[Depends(require_product("knowledge"))])
async def list_personal_items(
    limit: int = 50,
    offset: int = 0,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List personal knowledge artifacts for the authenticated user."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{settings.knowledge_ingest_url}/knowledge/v1/personal/items",
            headers={"x-internal-secret": settings.knowledge_ingest_secret},
            params={
                "org_id": org.zitadel_org_id,
                "user_id": zitadel_user_id,
                "limit": limit,
                "offset": offset,
            },
        )
    if not resp.is_success:
        logger.warning("knowledge-ingest list items failed: HTTP %s", resp.status_code)
        raise HTTPException(status_code=resp.status_code, detail="Failed to list personal items")
    return resp.json()


@router.delete("/personal/items/{artifact_id}", dependencies=[Depends(require_product("knowledge"))])
async def delete_personal_item(
    artifact_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete a personal knowledge artifact for the authenticated user."""
    zitadel_user_id, org, _caller = await _get_caller_org(credentials, db)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.delete(
            f"{settings.knowledge_ingest_url}/knowledge/v1/personal/items/{artifact_id}",
            headers={"x-internal-secret": settings.knowledge_ingest_secret},
            params={
                "org_id": org.zitadel_org_id,
                "user_id": zitadel_user_id,
            },
        )
    if not resp.is_success:
        logger.warning("knowledge-ingest delete item failed: HTTP %s", resp.status_code)
        raise HTTPException(status_code=resp.status_code, detail="Failed to delete personal item")
    return resp.json()
