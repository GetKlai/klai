"""App-facing routes for URL / Text sources (SPEC-KB-SOURCES-001).

Two thin routes under ``/api/app/knowledge-bases/{kb_slug}/sources/{type}``
that each:

1. Authenticate the caller via ``_get_caller_org`` (Zitadel bearer).
2. Resolve the KB in the caller's org (RLS-scoped) and assert write access.
3. Enforce the per-KB item quota via ``assert_can_add_item_to_kb``.
4. Run the matching extractor (URL → crawl4ai, Text → normalise).
5. Forward the extracted (title, content) pair to
   ``POST http://knowledge-ingest:8000/ingest/v1/document`` via
   ``knowledge_ingest_client.ingest_document``.

Error mapping follows SPEC D8. Structured logs include ``org_id``, ``kb_slug``,
``source_type``, ``duration_ms``, and hostname — NEVER the full URL (query
strings can leak tokens; SPEC R7.2).

The ``/sources/youtube`` route is retained as an HTTP 410 stub
(SPEC-KB-YOUTUBE-REMOVE-001) so any forgotten hard-coded caller surfaces
loudly in VictoriaLogs rather than silently 404'ing.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg
from app.services import knowledge_ingest_client
from app.services.access import get_user_role_for_kb
from app.services.kb_quota import assert_can_add_item_to_kb
from app.services.source_extractors.exceptions import (
    InvalidContentError,
    InvalidUrlError,
    SourceFetchError,
    SSRFBlockedError,
)
from app.services.source_extractors.text import extract_text
from app.services.source_extractors.url import extract_url

logger = structlog.get_logger()

router = APIRouter(prefix="/api/app", tags=["app-sources"])

_WRITE_ROLES = frozenset({"contributor", "owner"})


# --- Request / response models ---------------------------------------------


class UrlSourceRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class TextSourceRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=500_000)


class SourceIngestedResponse(BaseModel):
    artifact_id: str
    source_ref: str
    source_type: str


# --- Helpers ---------------------------------------------------------------


async def _get_writable_kb_or_raise(
    kb_slug: str,
    caller_id: str,
    org: PortalOrg,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Resolve the KB, assert caller has contributor+ role, and quota is OK."""
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org.id,
            PortalKnowledgeBase.slug == kb_slug,
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    role = await get_user_role_for_kb(
        kb_id=kb.id,
        user_id=caller_id,
        db=db,
        default_org_role=kb.default_org_role,
        kb_org_id=kb.org_id,
        kb_created_by=kb.created_by,
    )
    if role not in _WRITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Write access to this knowledge base is required",
        )

    # Raises HTTP 403 with error_code=kb_quota_items_exceeded when at limit.
    await assert_can_add_item_to_kb(kb, org)
    return kb


async def _forward_ingest(
    *,
    org: PortalOrg,
    kb: PortalKnowledgeBase,
    title: str,
    content: str,
    source_type: str,
    content_type: str,
    source_ref: str,
    extra: dict,
) -> str:
    """Build the IngestRequest payload and post it to knowledge-ingest."""
    payload: dict = {
        "org_id": org.zitadel_org_id,
        "kb_slug": kb.slug,
        "path": source_ref,  # unique per logical source; stable across re-submits
        "content": content,
        "title": title,
        "source_type": source_type,
        "content_type": content_type,
        "source_ref": source_ref,
        "kb_name": kb.name,
        "extra": extra,
    }
    try:
        return await knowledge_ingest_client.ingest_document(payload)
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "ingest_document_upstream_error",
            kb_slug=kb.slug,
            source_type=source_type,
            status=exc.response.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Knowledge ingest upstream error",
        ) from exc
    except httpx.RequestError as exc:
        logger.exception(
            "ingest_document_request_error",
            kb_slug=kb.slug,
            source_type=source_type,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Knowledge ingest unreachable",
        ) from exc


def _hostname(raw: str) -> str:
    try:
        return urlparse(raw).hostname or "?"
    except ValueError:
        return "?"


# --- Routes ----------------------------------------------------------------


@router.post(
    "/knowledge-bases/{kb_slug}/sources/url",
    response_model=SourceIngestedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_url_source(
    kb_slug: str,
    body: UrlSourceRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> SourceIngestedResponse:
    """Fetch a web page via crawl4ai and ingest its markdown."""
    start = time.monotonic()
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_writable_kb_or_raise(kb_slug, caller_id, org, db)

    try:
        title, content, source_ref = await extract_url(body.url)
    except InvalidUrlError as exc:
        raise HTTPException(status_code=400, detail="Not a valid URL") from exc
    except SSRFBlockedError as exc:
        raise HTTPException(status_code=400, detail="This URL is not allowed") from exc
    except SourceFetchError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not reach the page — try again",
        ) from exc

    artifact_id = await _forward_ingest(
        org=org,
        kb=kb,
        title=title,
        content=content,
        source_type="url",
        content_type="web_page",
        source_ref=source_ref,
        extra={"source_url": source_ref},
    )
    logger.info(
        "source_ingested",
        org_id=org.zitadel_org_id,
        kb_slug=kb_slug,
        source_type="url",
        hostname=_hostname(source_ref),
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    return SourceIngestedResponse(artifact_id=artifact_id, source_ref=source_ref, source_type="url")


@router.post(
    "/knowledge-bases/{kb_slug}/sources/youtube",
    status_code=status.HTTP_410_GONE,
)
async def add_youtube_source(
    kb_slug: str,
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """SPEC-KB-YOUTUBE-REMOVE-001: removed route, returns HTTP 410 Gone.

    YouTube ingest was disabled in SPEC-KB-SOURCES-001 v1.4.0 (UI tile
    pulled) and v1.5.0 (UI tile gone). The backend route stayed live as a
    "single-PR restore". In practice YouTube continued blocking core-01's
    datacenter IP and the residential-proxy fallback was never configured,
    so every real call returned 502. This SPEC removes the dead path.

    Auth still loads so the structlog event carries ``org_id`` for the
    caller — that lets us spot which tenant still has the route hard-coded.
    No upstream call, no extractor import, no quota burn.
    """

    caller_id, org, _ = await _get_caller_org(credentials, db)
    user_agent = request.headers.get("user-agent", "")
    logger.warning(
        "youtube_ingest_called_after_removal",
        org_id=org.zitadel_org_id,
        kb_slug=kb_slug,
        caller_id=caller_id,
        user_agent=user_agent[:200],  # truncate to keep log entries small
    )
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="youtube_ingest_removed",
    )


@router.post(
    "/knowledge-bases/{kb_slug}/sources/text",
    response_model=SourceIngestedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_text_source(
    kb_slug: str,
    body: TextSourceRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> SourceIngestedResponse:
    """Accept a plain-text paste and ingest it directly (no external fetch)."""
    start = time.monotonic()
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_writable_kb_or_raise(kb_slug, caller_id, org, db)

    try:
        title, content, source_ref = extract_text(body.title, body.content)
    except InvalidContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    artifact_id = await _forward_ingest(
        org=org,
        kb=kb,
        title=title,
        content=content,
        source_type="text",
        content_type="plain_text",
        source_ref=source_ref,
        extra={"original_title": (body.title or "").strip() or None},
    )
    logger.info(
        "source_ingested",
        org_id=org.zitadel_org_id,
        kb_slug=kb_slug,
        source_type="text",
        content_length=len(content),
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    return SourceIngestedResponse(artifact_id=artifact_id, source_ref=source_ref, source_type="text")
