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
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from minio import Minio
from minio.error import S3Error
from sqlalchemy import select

from app.api.partner_dependencies import PartnerAuthContext, get_partner_key
from app.api.session_deps import get_optional_session
from app.core.config import settings
from app.core.session import SessionContext
from app.models.knowledge_bases import PortalKnowledgeBase

logger = structlog.get_logger()

router = APIRouter(tags=["KB Images"])

_CACHE_CONTROL = "private, max-age=86400"
_STREAM_CHUNK_SIZE = 65536


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
    kb_slug: str,
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
        kb_result = await db.execute(
            select(PortalKnowledgeBase.id).where(
                PortalKnowledgeBase.org_id == auth_ctx.org_id,
                PortalKnowledgeBase.slug == kb_slug,
            )
        )
        kb_id = kb_result.scalar_one_or_none()
        if kb_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image not found",
            )
        if kb_id not in auth_ctx.kb_access:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
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
    Partner/widget callers must also have KB access for the path kb_slug.
    Streams from Garage S3 API (private, authenticated).
    Cache-Control: private, max-age=86400.
    """
    # Step 1: Resolve caller identity
    caller_org_id = await _resolve_caller_org_id(request, session, kb_slug)

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
