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
import uuid
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.api.dependencies import _load_org_or_500
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller
from app.core.profiles import ProfileRole
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services import (
    archive,
    docling_client,
    file_upload,
    kb_uploads_repo,
    knowledge_ingest_client,
)
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


class FileUploadEntry(BaseModel):
    """One accepted file. ``status`` is ``done`` for the text path
    (synchronous) or ``processing`` for the docling path (async).

    The frontend polls ``GET /sources/file/{id}/status`` while
    ``status == "processing"``. ``artifact_id`` is null until the
    upload reaches ``done``; ``failure_reason`` is null unless the row
    transitions to ``failed``.
    """

    id: uuid.UUID
    filename: str
    status: str
    source_type: str = "file"
    source_ref: str
    artifact_id: str | None = None
    failure_reason: str | None = None


class FileSourcesIngestedResponse(BaseModel):
    """Response for ``POST /sources/file``.

    Multi-file uploads return one entry per accepted file plus a
    ``skipped`` array per rejected file so the UI can show partial
    success without forcing the user to re-upload the rest.
    """

    uploads: list[FileUploadEntry]
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

# Hard cap on multipart parts per request. Keeps Starlette form parsing
# bounded and matches the SPEC's "max 10 files per submit" rule.
_MAX_FILES_PER_REQUEST = 10


def _failure_reason_from(exc: HTTPException) -> str:
    """Extract ``error_code`` from a structured ``HTTPException.detail``.

    Defaults to ``"invalid"`` when ``detail`` is not a dict — defensive
    against future raise sites that forget the structured shape.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        code = detail.get("error_code")
        if isinstance(code, str) and code:
            return code
    return "invalid"


async def _ingest_text_bytes(
    *,
    body: bytes,
    filename: str,
    ext: str,
    kb: PortalKnowledgeBase,
    org: Any,
    perms: UserPermissions,
    db: AsyncSession,
) -> tuple[FileUploadEntry | None, FileUploadSkippedEntry | None]:
    """Decode + ingest a text-path payload synchronously.

    Returns a one-of: either an accepted entry (status="done") OR a
    skipped entry. Never raises — all error paths surface via ``skipped``.
    """
    try:
        validated = file_upload.validate_text_upload(filename, body)
    except HTTPException as exc:
        return None, FileUploadSkippedEntry(filename=filename, reason=_failure_reason_from(exc), extension=ext)

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
            "bytes": validated.bytes_count,
            "pipeline": "text",
        },
    )

    upload_view = await kb_uploads_repo.create_upload(
        db,
        kb_id=kb.id,
        org_id=perms.org_id,
        created_by=perms.user_id,
        filename=filename,
        extension=ext,
        mime="text/plain",
        bytes_count=validated.bytes_count,
        source_ref=validated.source_ref,
        status=kb_uploads_repo.STATUS_DONE,
        artifact_id=artifact_id,
    )
    return (
        FileUploadEntry(
            id=upload_view.id,
            filename=filename,
            status=upload_view.status,
            source_ref=validated.source_ref,
            artifact_id=artifact_id,
        ),
        None,
    )


async def _ingest_docling_bytes(
    *,
    body: bytes,
    filename: str,
    ext: str,
    kb: PortalKnowledgeBase,
    perms: UserPermissions,
    db: AsyncSession,
) -> tuple[FileUploadEntry | None, FileUploadSkippedEntry | None]:
    """Magic-byte-validate + submit a docling-path payload.

    The function returns synchronously after docling-serve accepts the
    submission and returns a ``task_id``. The actual conversion runs in
    docling's worker queue; ``kb_upload_poller`` watches the task and
    transitions the row to ``done`` / ``failed`` once it terminates.
    """
    try:
        validated = file_upload.validate_binary_upload(filename, body)
    except HTTPException as exc:
        return None, FileUploadSkippedEntry(filename=filename, reason=_failure_reason_from(exc), extension=ext)

    try:
        submission = await docling_client.submit_file_async(
            filename=validated.filename,
            content=validated.content,
            content_type=validated.mime,
            input_format=validated.docling_format,
        )
    except docling_client.DoclingTimeoutError:
        logger.exception("docling_submit_timeout", filename=filename, extension=ext, bytes=validated.bytes_count)
        return None, FileUploadSkippedEntry(filename=filename, reason="docling_timeout", extension=ext)
    except docling_client.DoclingError:
        logger.exception("docling_submit_failed", filename=filename, extension=ext, bytes=validated.bytes_count)
        return None, FileUploadSkippedEntry(filename=filename, reason="extraction_failed", extension=ext)

    upload_view = await kb_uploads_repo.create_upload(
        db,
        kb_id=kb.id,
        org_id=perms.org_id,
        created_by=perms.user_id,
        filename=validated.filename,
        extension=validated.extension,
        mime=validated.mime,
        bytes_count=validated.bytes_count,
        source_ref=validated.source_ref,
        status=kb_uploads_repo.STATUS_PROCESSING,
        docling_task_id=submission.task_id,
    )
    return (
        FileUploadEntry(
            id=upload_view.id,
            filename=validated.filename,
            status=upload_view.status,
            source_ref=validated.source_ref,
        ),
        None,
    )


async def _dispatch_blob(
    *,
    body: bytes,
    filename: str,
    kb: PortalKnowledgeBase,
    org: Any,
    perms: UserPermissions,
    db: AsyncSession,
    allow_archive: bool,
) -> tuple[list[FileUploadEntry], list[FileUploadSkippedEntry]]:
    """Classify ``filename`` + dispatch the bytes to the right pipeline.

    Returns ``(accepted, skipped)``. ``allow_archive=False`` is used
    for archive entries to prevent nested-archive attacks.
    """
    accepted: list[FileUploadEntry] = []
    skipped: list[FileUploadSkippedEntry] = []

    try:
        ext, pipeline = file_upload.classify_extension(filename)
    except HTTPException as exc:
        skipped.append(FileUploadSkippedEntry(filename=filename, reason=_failure_reason_from(exc)))
        return accepted, skipped

    if pipeline == "phase_pending":
        skipped.append(FileUploadSkippedEntry(filename=filename, reason="phase_pending", extension=ext))
        return accepted, skipped

    if pipeline == "archive":
        if not allow_archive:
            skipped.append(FileUploadSkippedEntry(filename=filename, reason="archive_nested", extension=ext))
            return accepted, skipped
        # Extract once, recurse per entry. Whole-archive failures bubble
        # up as HTTPException to the caller so the route raises 400.
        try:
            extraction = archive.extract_archive(filename, body)
        except archive.ArchiveAbort as exc:
            reason = _failure_reason_from(exc)
            skipped.append(FileUploadSkippedEntry(filename=filename, reason=reason, extension=ext))
            return accepted, skipped

        for entry_skip in extraction.skipped:
            skipped.append(FileUploadSkippedEntry(filename=entry_skip.filename, reason=entry_skip.reason))

        for member in extraction.extracted:
            inner_accepted, inner_skipped = await _dispatch_blob(
                body=member.content,
                filename=member.filename,
                kb=kb,
                org=org,
                perms=perms,
                db=db,
                allow_archive=False,
            )
            accepted.extend(inner_accepted)
            skipped.extend(inner_skipped)

        if not extraction.extracted and not extraction.skipped:
            skipped.append(FileUploadSkippedEntry(filename=filename, reason="archive_empty", extension=ext))
        return accepted, skipped

    if pipeline == "text":
        entry, skip = await _ingest_text_bytes(
            body=body, filename=filename, ext=ext, kb=kb, org=org, perms=perms, db=db
        )
    else:  # docling
        entry, skip = await _ingest_docling_bytes(body=body, filename=filename, ext=ext, kb=kb, perms=perms, db=db)

    if entry is not None:
        accepted.append(entry)
    elif skip is not None:
        skipped.append(skip)
    return accepted, skipped


@router.post(
    "/knowledge-bases/{kb_slug}/sources/file",
    response_model=FileSourcesIngestedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_file_source(
    kb_slug: str,
    request: Request,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> FileSourcesIngestedResponse:
    """Accept a multipart file upload and ingest it into the KB.

    SPEC-KB-FILE-UPLOAD-001. Each file is classified into one of four
    pipelines:

    - **text** (``.md / .txt / .csv``): decoded and forwarded to
      ``/ingest/v1/document`` synchronously. ``kb_uploads.status=done``.
    - **docling** (``.pdf / .docx / .xlsx / .pptx / .json / .xml``):
      magic-byte validated and submitted to docling-serve's async
      queue. ``kb_uploads.status=processing`` with the task_id; the
      poller advances it to ``done`` / ``failed`` once docling finishes.
    - **archive** (``.zip / .tar``): safely extracted via
      ``app.services.archive`` (sunzip-style guards) and each member
      recursed through this dispatcher (``allow_archive=False``).
    - **phase_pending** (``.doc``): not yet implemented. Returned as
      ``skipped[].reason = "phase_pending"`` so the UI can show a
      localised "binnenkort" message.

    Multi-file requests partially succeed: accepted files appear in
    ``uploads`` (with their per-file status), rejected files in
    ``skipped``. The endpoint returns 400 only when no file is
    acceptable, so the frontend can surface a coherent error.

    The route does its own ``request.form()`` call rather than relying
    on FastAPI's ``files: list[UploadFile] = File(...)`` injection
    because the default ``max_part_size`` is 1 MB — Starlette 1.0's
    multipart parser enforces that on every part including file parts,
    so a 128 MB PDF mid-stream causes the parser to drop the connection
    (Caddy then logs "use of closed network connection" → 502). We
    raise the cap to ``MAX_BINARY_FILE_BYTES + 4 KiB`` so a 200 MB
    member uploads cleanly.

    NOTE: the ``UploadFile`` type used in the ``isinstance`` filter MUST
    come from ``starlette.datastructures`` — ``fastapi.UploadFile`` is a
    subclass, so ``isinstance(starlette_obj, fastapi.UploadFile)`` returns
    ``False`` and silently filters every parsed file out of the list.
    """
    # Override Starlette's 1 MB default per-part cap. Adding 4 KiB
    # accounts for the multipart envelope (boundary, headers, CRLFs).
    try:
        form = await request.form(
            max_files=_MAX_FILES_PER_REQUEST,
            max_part_size=file_upload.MAX_BINARY_FILE_BYTES + 4 * 1024,
        )
    except Exception as exc:
        # Starlette MultiPartException + httpx parse errors land here.
        # The most common is the user uploading > 200 MB; surface as
        # 413 with the structured error code rather than 500.
        logger.warning("kb_upload_form_parse_failed", error=str(exc), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error_code": "file_too_large",
                "max_bytes": file_upload.MAX_BINARY_FILE_BYTES,
            },
        ) from exc

    files: list[UploadFile] = [v for v in form.getlist("files") if isinstance(v, UploadFile)]

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

    accepted: list[FileUploadEntry] = []
    skipped: list[FileUploadSkippedEntry] = []

    for upload in files:
        filename = file_upload.sanitize_filename(upload.filename)
        # Read the multipart part once. Starlette spools to disk above
        # 1 MB so a 200 MB upload never holds 200 MB of RSS.
        body = await upload.read(file_upload.MAX_BINARY_FILE_BYTES + 1)

        per_file_accepted, per_file_skipped = await _dispatch_blob(
            body=body,
            filename=filename,
            kb=kb,
            org=org,
            perms=perms,
            db=db,
            allow_archive=True,
        )
        accepted.extend(per_file_accepted)
        skipped.extend(per_file_skipped)

        for entry in per_file_accepted:
            logger.info(
                "kb_upload_received",
                org_id=org.zitadel_org_id,
                kb_slug=kb_slug,
                filename=entry.filename,
                upload_id=str(entry.id),
                decision="accepted",
                status=entry.status,
            )
        for skip in per_file_skipped:
            logger.info(
                "kb_upload_received",
                org_id=org.zitadel_org_id,
                kb_slug=kb_slug,
                filename=skip.filename,
                extension=skip.extension,
                decision="rejected",
                failure_reason=skip.reason,
            )

    # Commit any kb_uploads INSERTs while the request session still has
    # the tenant context bound. SQLAlchemy's autocommit-on-cleanup runs
    # AFTER `Depends(get_caller)` resets the GUC — without an explicit
    # flush+commit here, cat-D RLS would silently drop the inserts.
    await db.commit()

    if not accepted:
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


# --- Status polling endpoint -----------------------------------------------


class FileUploadStatusResponse(BaseModel):
    """Per-row status snapshot returned to the frontend poller."""

    id: uuid.UUID
    filename: str
    status: str
    source_ref: str
    artifact_id: str | None = None
    failure_reason: str | None = None


@router.get(
    "/knowledge-bases/{kb_slug}/sources/file/{upload_id}/status",
    response_model=FileUploadStatusResponse,
)
async def get_file_source_status(
    kb_slug: str,
    upload_id: uuid.UUID,
    perms: UserPermissions = Depends(get_caller),
    db: AsyncSession = Depends(get_db),
) -> FileUploadStatusResponse:
    """Return the current status for a single upload.

    Tenant-scoped via the request session's ``app.current_org_id``
    GUC; cat-D RLS filters cross-org rows so an attacker that guesses
    a UUID still gets a 404.
    """
    # Resolve the KB to enforce kb-existence + write-access (404 not 403
    # for cross-org per portal-security.md).
    await _get_writable_kb_or_raise(kb_slug, perms, db)

    view = await kb_uploads_repo.get_view(db, upload_id=upload_id)
    if view is None or view.kb_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")

    return FileUploadStatusResponse(
        id=view.id,
        filename=view.filename,
        status=view.status,
        source_ref=view.source_ref,
        artifact_id=view.artifact_id,
        failure_reason=view.failure_reason,
    )
