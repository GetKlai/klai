"""App-facing account preferences API.

GET  /api/app/account/kb-preference  — read current KB scope preference
PATCH /api/app/account/kb-preference — update KB scope preference

The PATCH endpoint validates that all submitted kb_slugs belong to the caller's org,
increments kb_pref_version, and immediately invalidates the LiteLLM Redis cache key
so the next LLM call picks up the new settings without delay.
"""

import asyncio
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.config import settings
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app/account", tags=["app-account"])


async def _invalidate_litellm_kb_cache(org_id: int, librechat_user_id: str) -> None:
    """Delete the LiteLLM version pointer key so the next LLM call fetches fresh KB prefs.

    Fire-and-forget — failures are logged but never bubble up to the caller.
    Key format mirrors klai_knowledge.py: kb_ver:{org_id}:{user_id}.
    """
    try:
        r = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            socket_connect_timeout=1.0,
        )
        async with r:
            await r.delete(f"kb_ver:{org_id}:{librechat_user_id}")
    except Exception as exc:
        logger.warning(
            "KB pref: Redis cache invalidation failed (%s) — hook picks up within 30s",
            exc,
            exc_info=True,
        )


# -- Pydantic schemas ---------------------------------------------------------


class KBPreferenceOut(BaseModel):
    kb_retrieval_enabled: bool
    kb_personal_enabled: bool
    kb_slugs_filter: list[str] | None
    kb_narrow: bool
    kb_pref_version: int


class KBPreferencePatch(BaseModel):
    kb_retrieval_enabled: bool | None = None
    kb_personal_enabled: bool | None = None
    kb_slugs_filter: list[str] | None = None
    kb_narrow: bool | None = None


# -- Endpoints ----------------------------------------------------------------


@router.get("/kb-preference", response_model=KBPreferenceOut)
async def get_kb_preference(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> KBPreferenceOut:
    """Return the caller's current KB scope preference."""
    _, _, user = await _get_caller_org(credentials, db)
    return KBPreferenceOut(
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_narrow=user.kb_narrow,
        kb_pref_version=user.kb_pref_version,
    )


@router.patch("/kb-preference", response_model=KBPreferenceOut)
async def patch_kb_preference(
    body: KBPreferencePatch,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> KBPreferenceOut:
    """Update the caller's KB scope preference.

    Validates that any submitted kb_slugs belong to the caller's own org.
    Empty list is normalized to null (null means all org KBs).
    Increments kb_pref_version on every successful save.
    """
    _, org, user = await _get_caller_org(credentials, db)

    if body.kb_retrieval_enabled is not None:
        user.kb_retrieval_enabled = body.kb_retrieval_enabled

    if body.kb_personal_enabled is not None:
        user.kb_personal_enabled = body.kb_personal_enabled

    if body.kb_narrow is not None:
        user.kb_narrow = body.kb_narrow

    if "kb_slugs_filter" in body.model_fields_set:
        slugs = body.kb_slugs_filter

        # Normalize empty list to null (empty list would mean "no org KBs", null means "all")
        if slugs is not None and len(slugs) == 0:
            slugs = None

        if slugs is not None:
            # Validate all slugs belong to the caller's org (REQ-N3)
            result = await db.execute(
                select(PortalKnowledgeBase.slug).where(
                    PortalKnowledgeBase.org_id == org.id,
                    PortalKnowledgeBase.slug.in_(slugs),
                )
            )
            valid_slugs = {row[0] for row in result}
            invalid = set(slugs) - valid_slugs
            if invalid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unknown KB slugs for this org: {sorted(invalid)}",
                )

        user.kb_slugs_filter = slugs

    user.kb_pref_version += 1
    await db.commit()

    if user.librechat_user_id:
        asyncio.get_running_loop().create_task(_invalidate_litellm_kb_cache(org.id, user.librechat_user_id))

    return KBPreferenceOut(
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_narrow=user.kb_narrow,
        kb_pref_version=user.kb_pref_version,
    )
