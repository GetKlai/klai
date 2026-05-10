"""App-facing routes for URL / Text sources (SPEC-KB-SOURCES-001).

Two thin routes under ``/api/app/knowledge-bases/{kb_slug}/sources/{type}``
that each:

1. Authenticate the caller via ``Depends(get_caller)`` (Zitadel bearer).
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
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.core.profiles import ProfileRole
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services import file_upload, knowledge_ingest_client
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


class FileUploadSkippedEntry(BaseModel):
    """One file that was rejected during a multi-file upload."""

    filename: str
    reason: str
    extension: str | None = None


class FileSourcesIngestedResponse(BaseModel):
    """Response for ``POST /sources/file``.

    Multi-file uploads return one entry per accepted file plus a
    ``skipped`` array per rejected file so the UI can show partial
    success without forcing the user to re-upload the rest.
    """

    uploads: list[SourceIngestedResponse]
    skipped: list[FileUploadSkippedEntry]


# --- Helpers ---------------------------------------------------------------


async def _get_writable_kb_or_raise(
    kb_slug: str,
    perms: UserPermissions,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Resolve the KB, assert caller has contributor+ role, and quota is OK."""
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == perms.org_id,
            PortalKnowledgeBase.slug == kb_slug,
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )

    # REQ-7: personal effective_role MUST NOT write to org-owned KBs.
    # The check fires before role lookup because effective_role already
    # encodes the plan-vs-profile decision; "personal" cannot earn write
    # access via group/user grants on an org-owned KB.
    if kb.owner_type == "org" and perms.effective_role == ProfileRole.PERSONAL:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "org_kb_write_requires_company"},
        )

    role = await get_user_role_for_kb(
        kb_id=kb.id,
        user_id=perms.user_id,
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

    org = await _load_org_or_500(db, perms.org_id)
    # Raises HTTP 403 with error_code=kb_quota_items_exceeded when at limit.
    await assert_can_add_item_to_kb(kb, org, role=perms.role.value)
    return kb


async def _forward_ingest(
    *,
    zitadel_org_id: str,
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
        "org_id": zitadel_org_id,
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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SourceIngestedResponse:
    """Fetch a web page via crawl4ai and ingest its markdown."""
    start = time.monotonic()
    kb = await _get_writable_kb_or_raise(kb_slug, perms, db)
    org = await _load_org_or_500(db, perms.org_id)

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
        zitadel_org_id=org.zitadel_org_id,
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
    perms: UserPermissions = Depends(get_caller),
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

    user_agent = request.headers.get("user-agent", "")
    logger.warning(
        "youtube_ingest_called_after_removal",
        org_id=perms.org_id,
        kb_slug=kb_slug,
        caller_id=perms.user_id,
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
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> SourceIngestedResponse:
    """Accept a plain-text paste and ingest it directly (no external fetch)."""
    start = time.monotonic()
    kb = await _get_writable_kb_or_raise(kb_slug, perms, db)
    org = await _load_org_or_500(db, perms.org_id)

    try:
        title, content, source_ref = extract_text(body.title, body.content)
    except InvalidContentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    artifact_id = await _forward_ingest(
        zitadel_org_id=org.zitadel_org_id,
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


# --- File route ------------------------------------------------------------

# SPEC-KB-FILE-UPLOAD-001 Phase 1A: text-path only via multipart upload.
# .md / .txt / .csv route through the existing /ingest/v1/document text
# pipeline. .pdf / .docx / .pptx / .xlsx / .json / .xml / .zip / .tar / .doc
# are recognised but return ``phase_pending`` per file in the ``skipped``
# array; the frontend maps that to a localised "binnenkort beschikbaar"
# message instead of the previous 500-on-Gitea-wiki-upload bug.

# Hard cap on multipart parts per request. Keeps Starlette form parsing
# bounded and matches REQ-2 (max 10 files per submit).
_MAX_FILES_PER_REQUEST = 10


@router.post(
    "/knowledge-bases/{kb_slug}/sources/file",
    response_model=FileSourcesIngestedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_file_source(
    kb_slug: str,
    files: list[UploadFile] = File(...),
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> FileSourcesIngestedResponse:
    """Accept a multipart file upload and ingest it into the KB.

    SPEC-KB-FILE-UPLOAD-001 Phase 1A. The endpoint validates each file's
    extension, decodes text-path bytes to UTF-8, and forwards the
    normalised ``(title, content)`` to the same
    ``/ingest/v1/document`` endpoint that ``/sources/text`` uses. Binary
    formats are recorded as ``skipped`` with reason ``phase_pending``
    so the UI can surface the limitation without faking success.

    Multi-file requests partially succeed: accepted files are ingested,
    rejected files appear in ``skipped``. The endpoint returns 400 only
    when no file is acceptable.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_code": "no_files"},
        )
    if len(files) > _MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": "too_many_files",
                "max": _MAX_FILES_PER_REQUEST,
                "received": len(files),
            },
        )

    start = time.monotonic()
    kb = await _get_writable_kb_or_raise(kb_slug, perms, db)
    org = await _load_org_or_500(db, perms.org_id)

    accepted: list[SourceIngestedResponse] = []
    skipped: list[FileUploadSkippedEntry] = []

    for upload in files:
        filename = (upload.filename or "").strip() or "untitled"

        try:
            ext, phase = file_upload.classify_extension(filename)
        except HTTPException as exc:
            detail: Any = exc.detail
            reason = detail.get("error_code") if isinstance(detail, dict) else "unsupported_extension"
            skipped.append(FileUploadSkippedEntry(filename=filename, reason=reason or "unsupported_extension"))
            logger.info(
                "kb_upload_received",
                org_id=org.zitadel_org_id,
                kb_slug=kb_slug,
                filename=filename,
                decision="rejected",
                failure_reason=reason,
            )
            continue

        if phase == "phase_pending":
            skipped.append(FileUploadSkippedEntry(filename=filename, reason="phase_pending", extension=ext))
            logger.info(
                "kb_upload_received",
                org_id=org.zitadel_org_id,
                kb_slug=kb_slug,
                filename=filename,
                extension=ext,
                decision="rejected",
                failure_reason="phase_pending",
            )
            continue

        # Read up to MAX+1 so the size guard catches the boundary correctly.
        body = await upload.read(file_upload.MAX_TEXT_FILE_BYTES + 1)
        try:
            validated = file_upload.validate_text_upload(filename, body)
        except HTTPException as exc:
            detail = exc.detail
            reason = detail.get("error_code") if isinstance(detail, dict) else "invalid"
            skipped.append(FileUploadSkippedEntry(filename=filename, reason=reason or "invalid", extension=ext))
            logger.info(
                "kb_upload_received",
                org_id=org.zitadel_org_id,
                kb_slug=kb_slug,
                filename=filename,
                extension=ext,
                decision="rejected",
                failure_reason=reason,
            )
            continue

        artifact_id = await _forward_ingest(
            zitadel_org_id=org.zitadel_org_id,
            kb=kb,
            title=validated.title,
            content=validated.content,
            source_type="file",
            content_type="plain_text",
            source_ref=validated.source_ref,
            extra={
                "original_filename": filename,
                "extension": ext,
                "phase": "1a",
                "bytes": validated.bytes_count,
            },
        )
        accepted.append(
            SourceIngestedResponse(
                artifact_id=artifact_id,
                source_ref=validated.source_ref,
                source_type="file",
            )
        )
        logger.info(
            "kb_upload_received",
            org_id=org.zitadel_org_id,
            kb_slug=kb_slug,
            filename=filename,
            extension=ext,
            bytes=validated.bytes_count,
            decision="accepted",
        )

    if not accepted:
        # Every file was rejected. Surface a 400 with the first reason so
        # the UI shows a coherent error instead of a confusing 202-with-
        # zero-uploads response.
        first_reason = skipped[0].reason if skipped else "no_accepted_files"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_code": first_reason,
                "skipped": [s.model_dump() for s in skipped],
            },
        )

    logger.info(
        "file_sources_ingested",
        org_id=org.zitadel_org_id,
        kb_slug=kb_slug,
        accepted_count=len(accepted),
        skipped_count=len(skipped),
        duration_ms=int((time.monotonic() - start) * 1000),
    )
    return FileSourcesIngestedResponse(uploads=accepted, skipped=skipped)
