"""KB-image auth-proxy route -- SPEC-TI-009 / finding B-4.

Replaces anonymous Caddy -> Garage website-mode read with auth-proxied stream.
Verifies the requesting caller has access to the org_id encoded in the path,
then streams the image bytes directly from Garage S3 API (private, authenticated).

Authorization model (checked in order):
1. BFF session cookie  - session.org_id == path[org_id]
2. Widget session JWT  - org_id in the token allowed org set
3. Partner API key     - key org_id == path[org_id]

If none matches, returns 401 (unauthenticated) or 403 (wrong org).

Object key format (unchanged from SPEC-KB-IMAGE-002):
    {org_id}/images/{kb_slug}/{sha256}.{ext}

Cache-Control: private, max-age=86400.

[DRAFT] Widget path: session-cookie and partner-key callers are fully supported.
Widget session token callers go through _auth_via_session_token in
partner_dependencies and are covered by the partner-key code path below.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from klai_image_storage import ImageStore
from klai_image_storage.storage import MAX_IMAGE_SIZE
from minio import Minio
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app_knowledge_bases import _get_kb_or_404
from app.api.partner_dependencies import PartnerAuthContext, get_partner_key
from app.api.session_deps import get_optional_session
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller_at_least
from app.core.profiles import ProfileRole
from app.core.session import SessionContext

logger = structlog.get_logger()

router = APIRouter(tags=["KB Images"])

_CACHE_CONTROL = "private, max-age=86400"
_STREAM_CHUNK_SIZE = 65536

# MIME -> file extension mapping for the content-addressed key
# (the ImageStore lib normalises the ext internally, but we want stable suffixes).
_MIME_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def _make_minio_client() -> Minio:
    """Build a Minio client from settings (private S3 port :3900, not :3902)."""
    return Minio(
        settings.garage_s3_endpoint,
        access_key=settings.garage_s3_access_key,
        secret_key=settings.garage_s3_secret_key,
        region="garage",
        secure=False,
    )


def _stat_object(client: Minio, bucket: str, object_key: str) -> str:
    """Stat the S3 object; return Content-Type.

    Raises HTTPException(404) if the key does not exist.
    Raises HTTPException(502) on unexpected S3 errors.
    """
    try:
        stat = client.stat_object(bucket, object_key)
        return stat.content_type or "application/octet-stream"
    except S3Error as exc:
        if exc.code in ("NoSuchKey", "NoSuchBucket"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image not found",
            ) from exc
        logger.exception("kb_image_stat_error", object_key=object_key, error_code=exc.code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Storage error",
        ) from exc


async def _stream_object(client: Minio, bucket: str, object_key: str) -> AsyncIterator[bytes]:
    """Async generator that streams S3 object bytes in chunks via asyncio.to_thread.

    Existence is guaranteed by the prior _stat_object call in get_kb_image.
    Unexpected S3 errors during streaming are logged but cannot be converted to
    a proper HTTP error because the response headers have already been sent.
    """
    try:
        response = await asyncio.to_thread(client.get_object, bucket, object_key)
    except S3Error as exc:
        logger.exception("kb_image_s3_stream_error", object_key=object_key, error_code=exc.code)
        # Cannot raise HTTPException here -- response headers already sent.
        return

    try:
        while True:
            chunk = await asyncio.to_thread(response.read, _STREAM_CHUNK_SIZE)
            if not chunk:
                break
            yield chunk
    finally:
        await asyncio.to_thread(response.close)
        await asyncio.to_thread(response.release_conn)


async def _resolve_caller_org_id(
    request: Request,
    session: SessionContext | None,
) -> int:
    """Resolve the org_id of the caller from session or partner key.

    Raises HTTPException(401) when the caller is not authenticated.
    """
    if session is not None:
        if session.org_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="session_not_finalized",
            )
        return session.org_id

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="cookie_required",
        )

    from app.core.database import get_db

    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        auth_ctx: PartnerAuthContext = await get_partner_key(request, db)
        return auth_ctx.org_id
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("kb_image_partner_auth_error")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from exc
    finally:
        try:
            await db_gen.aclose()
        except Exception:
            logger.debug("kb_image_db_aclose_error", exc_info=True)


# @MX:ANCHOR: KB-image auth-proxy endpoint -- AC-1 through AC-5 of SPEC-TI-009
# @MX:REASON: Single enforcement point for cross-tenant image access control.
#   Every image fetch from the browser goes through this handler after the
#   Caddy backend is switched from garage:3902 to portal-api:8000.
#   Changing the org_id check or the S3 key format breaks tenant isolation.
# @MX:SPEC: SPEC-TI-009, finding B-4
@router.get("/kb-images/{org_id}/{kb_slug}/{filename}")
async def get_kb_image(
    org_id: int,
    kb_slug: str,
    filename: str,
    request: Request,
    session: SessionContext | None = Depends(get_optional_session),
) -> StreamingResponse:
    """Auth-proxied KB-image read (SPEC-TI-009 AC-1).

    Authorization: session.org_id or partner key org_id must equal path org_id.
    Streams from Garage S3 API (private, authenticated).
    Cache-Control: private, max-age=86400.
    """
    # Step 1: Resolve caller identity
    caller_org_id = await _resolve_caller_org_id(request, session)

    # Step 2: Authorize -- caller org MUST match path org_id (AC-5)
    if caller_org_id != org_id:
        logger.warning(
            "kb_image_cross_tenant_blocked",
            caller_org_id=caller_org_id,
            path_org_id=org_id,
            kb_slug=kb_slug,
            filename=filename,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Step 3: Build Garage S3 object key (format from SPEC-KB-IMAGE-002)
    object_key = f"{org_id}/images/{kb_slug}/{filename}"

    # Step 4: Guard against unconfigured Garage endpoint (dev / test env)
    if not settings.garage_s3_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image storage not configured",
        )

    client = _make_minio_client()

    content_type = await asyncio.to_thread(_stat_object, client, settings.garage_kb_bucket, object_key)

    structlog.contextvars.bind_contextvars(org_id=org_id, kb_slug=kb_slug)

    # Step 5: Return StreamingResponse with cache headers (AC-1)
    return StreamingResponse(
        _stream_object(client, settings.garage_kb_bucket, object_key),
        media_type=content_type,
        headers={
            "Cache-Control": _CACHE_CONTROL,
            "X-Content-Type-Options": "nosniff",
        },
    )


# @MX:ANCHOR: KB-image upload — single enforcement point for tenant-scoped writes
# @MX:REASON: Changing the auth dependency, the kb_slug → org_id binding via
#   _get_kb_or_404, the SVG-reject branch, or the magic-byte MIME check breaks
#   tenant isolation or opens an XSS path via inline SVG. The 5 MB hard cap also
#   serves as memory-DoS guard for parallel uploads.
# @MX:SPEC: SPEC-PORTAL-DOCS-IMAGE-PASTE-001
@router.post("/kb-images/{kb_slug}")
async def upload_kb_image(
    request: Request,
    kb_slug: str,
    file: UploadFile,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.PERSONAL)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    """Accept a multipart image upload for a KB and store it in Garage S3.

    Auth model:
    - Caller MUST have an authenticated portal session (BFF cookie).
    - org_id is taken from ``perms.org_id`` (NOT from a path parameter).
    - kb_slug MUST resolve to a KB belonging to caller.org_id, enforced via
      :func:`_get_kb_or_404`. Cross-tenant attempts return 404 (per
      portal-security.md "never leak existence").

    Storage:
    - Body capped at 5 MB (``MAX_IMAGE_SIZE`` from klai_image_storage).
    - Magic-byte MIME validation via ``ImageStore.validate_image``.
    - SVG hard-rejected (REQ-5): the read-route streams images inline without
      CSP; an SVG-with-<script> would XSS on direct URL navigation. Connectors
      still accept SVG (different trust boundary).
    - Object key follows the existing read-route format
      ``{org_id}/images/{kb_slug}/{sha256}.{ext}``; identical bytes dedupe via
      content addressing.

    Response: ``{"url": "/kb-images/...", "deduplicated": bool}`` where ``url``
    is the relative path served by the GET endpoint above.
    """
    # Step 1: KB-scope authorization. Cross-tenant attempts surface as 404
    # from the helper's WHERE clause; we wrap to emit an observability warning
    # (REQ-7) for security monitoring before re-raising.
    try:
        kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            logger.warning(
                "kb_image_upload_cross_tenant_blocked",
                caller_org_id=perms.org_id,
                kb_slug=kb_slug,
            )
        raise

    # Step 2: Feature-flag guard. Empty endpoint => Garage not configured
    # (see python.md "Feature flag via empty env var").
    if not settings.garage_s3_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image storage not configured",
        )

    # Step 3a: Early size guard via Content-Length header.
    # FastAPI/Starlette has NO default body-size limit; a malicious client
    # could send Content-Length: 100000000 and force the server to read 100 MB
    # into memory before we ever reach the post-read len() check. Reject early.
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > MAX_IMAGE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Image too large (max 5 MB)",
                )
        except ValueError:
            # Malformed Content-Length — fall through; the post-read check is the safety net.
            pass

    # Step 3b: Read body + canonical size guard (REQ-3). Still required because:
    # (a) Content-Length is optional and can be omitted by clients,
    # (b) chunked transfer-encoding has no Content-Length,
    # (c) a malformed Content-Length falls through to here.
    data = await file.read()
    if len(data) > MAX_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Image too large (max 5 MB)",
        )

    # Step 4: Magic-byte MIME validation (REQ-4) + SVG hard-reject (REQ-5).
    mime = ImageStore.validate_image(data)
    if not mime:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported image type",
        )
    if mime == "image/svg+xml":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="SVG uploads not supported",
        )

    ext = _MIME_EXT.get(mime)
    if ext is None:
        # validate_image returned a MIME we have no extension for — defensive.
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported image type",
        )

    # Step 5: Upload via the shared lib (content-addressed dedup).
    store = ImageStore(
        endpoint=settings.garage_s3_endpoint,
        access_key=settings.garage_s3_access_key,
        secret_key=settings.garage_s3_secret_key,
        bucket=settings.garage_kb_bucket,
    )
    result = await store.upload_image(str(perms.org_id), kb_slug, data, ext)

    logger.info(
        "kb_image_uploaded",
        org_id=perms.org_id,
        kb_slug=kb_slug,
        kb_id=kb.id,
        size=len(data),
        object_key=result.object_key,
        deduplicated=result.deduplicated,
        mime=mime,
    )

    return {
        "url": f"/{result.public_url.lstrip('/')}",
        "deduplicated": result.deduplicated,
    }
