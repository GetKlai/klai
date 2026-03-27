"""App-facing account preferences API.

GET  /api/app/account/kb-preference  — read current KB scope preference
PATCH /api/app/account/kb-preference — update KB scope preference

The PATCH endpoint validates that all submitted kb_slugs belong to the caller's org
and increments kb_pref_version so the LiteLLM hook picks up the change within 30s.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/app/account", tags=["app-account"])


# -- Pydantic schemas ---------------------------------------------------------


class KBPreferenceOut(BaseModel):
    kb_retrieval_enabled: bool
    kb_personal_enabled: bool
    kb_slugs_filter: list[str] | None
    kb_pref_version: int


class KBPreferencePatch(BaseModel):
    kb_retrieval_enabled: bool | None = None
    kb_personal_enabled: bool | None = None
    kb_slugs_filter: list[str] | None = None


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

    return KBPreferenceOut(
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_pref_version=user.kb_pref_version,
    )
