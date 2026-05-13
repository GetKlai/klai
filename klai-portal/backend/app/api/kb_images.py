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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app_knowledge_bases import _get_kb_or_404
from app.api.dependencies import get_kb_with_access
from app.api.partner_dependencies import PartnerAuthContext, get_partner_key
from app.api.session_deps import get_optional_session
from app.core.config import settings
from app.core.database import get_db
from app.core.kb_image_url import KbImage
from app.core.permissions import UserPermissions, get_caller_at_least
from app.core.profiles import ProfileRole
from app.core.session import SessionContext
from app.models.portal import PortalOrg

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


async def _resolve_zitadel_org_id(org_id: int, db: AsyncSession) -> str:
    """Look up the zitadel_org_id for a given portal_orgs.id.

    The kb-images S3 key prefix uses zitadel_org_id (string) because that's
    the canonical tenant id in the knowledge domain (knowledge.artifacts.org_id
    is text, _rls_current_org_id() returns text). Auth-flow gives us
    portal_orgs.id (numeric) from the session — we resolve via portal_orgs
    once per request.

    Raises HTTPException(404) if the org is gone (e.g. mid-deprovision).
    """
    result = await db.execute(select(PortalOrg.zitadel_org_id).where(PortalOrg.id == org_id))
    zitadel = result.scalar_one_or_none()
    if zitadel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )
    return zitadel


# @MX:ANCHOR: KB-image auth-proxy endpoint — single enforcement point for
#   cross-tenant image access control. Every image fetch from the browser
#   goes through this handler. Changing the org_id check or the S3 key
#   format breaks tenant isolation.
#
#   The route path is sourced from KbImage.ROUTE_TEMPLATE (the single source
#   of truth for kb-image URL shapes per SPEC-KB-IMAGES-V2-001). Direct
#   string-literals here are forbidden and caught by
#   ``rules/no-hardcoded-kb-image-path.yml``. A drift between this route
#   declaration and what ``KbImage(...).public_path`` returns is caught at
#   portal-api boot by ``_assert_kb_image_routes_match_value_class`` in
#   ``app.main`` — the service refuses to start.
# @MX:SPEC: SPEC-KB-IMAGES-V2-001 REQ-2 (was SPEC-TI-009)
@router.get(KbImage.ROUTE_TEMPLATE)
async def get_kb_image(
    zitadel_org_id: str,
    kb_slug: str,
    filename: str,
    request: Request,
    session: SessionContext | None = Depends(get_optional_session),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Auth-proxied KB-image read.

    Authorization: caller's zitadel_org_id (looked up from session.org_id or
    partner key) MUST equal the path's zitadel_org_id. Streams from Garage
    S3 API (private, authenticated). Cache-Control: private, max-age=86400.

    The path uses zitadel_org_id (an 18-digit string) because that's the
    canonical tenant id in the knowledge domain — the connector + crawler
    pipelines both write S3 keys under {zitadel_org_id}/... and Caddy
    serves browser fetches with the same prefix.
    """
    # Step 1: Resolve caller identity → portal_orgs.id (int)
    caller_org_id = await _resolve_caller_org_id(request, session)

    # Step 2: Look up the caller's zitadel_org_id (S3 keys use the zitadel id)
    caller_zitadel_org_id = await _resolve_zitadel_org_id(caller_org_id, db)

    # Step 3: Authorize — caller's zitadel org MUST match path org_id (AC-5)
    if caller_zitadel_org_id != zitadel_org_id:
        logger.warning(
            "kb_image_cross_tenant_blocked",
            caller_org_id=caller_org_id,
            caller_zitadel_org_id=caller_zitadel_org_id,
            path_org_id=zitadel_org_id,
            kb_slug=kb_slug,
            filename=filename,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Step 4: Build Garage S3 object key (format from SPEC-KB-IMAGE-002 via KbImage)
    object_key = f"{zitadel_org_id}/images/{kb_slug}/{filename}"

    # Step 5: Guard against unconfigured Garage endpoint (dev / test env)
    if not settings.garage_s3_endpoint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image storage not configured",
        )

    client = _make_minio_client()

    content_type = await asyncio.to_thread(_stat_object, client, settings.garage_kb_bucket, object_key)

    structlog.contextvars.bind_contextvars(org_id=zitadel_org_id, kb_slug=kb_slug)

    # Step 6: Return StreamingResponse with cache headers (AC-1)
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
#   serves as memory-DoS guard for parallel uploads. The route path is sourced
#   from KbImage.UPLOAD_ROUTE_TEMPLATE (SPEC-KB-IMAGES-V2-001 REQ-2).
# @MX:SPEC: SPEC-PORTAL-DOCS-IMAGE-PASTE-001 + SPEC-KB-IMAGES-V2-001 REQ-2
@router.post(KbImage.UPLOAD_ROUTE_TEMPLATE, dependencies=[Depends(get_kb_with_access)])
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

    Response: ``{"url": kb_image_path, "deduplicated": bool}`` where the URL
    is the relative path served by the GET endpoint above (KbImage.public_path).
    """
    # Step 1: KB-scope authorization. A 404 here is the strong-or-weak signal:
    # either the kb_slug truly doesn't exist in the caller's org (typo) OR it
    # belongs to a different org (cross-tenant attempt). We cannot distinguish
    # without a second cross-org query, which would be expensive AND violate
    # the "never leak existence" rule. So we emit a neutral observability
    # event and let downstream alerting decide based on rate / pattern.
    try:
        kb = await _get_kb_or_404(kb_slug, perms.org_id, db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            logger.warning(
                "kb_image_upload_kb_not_found",
                caller_org_id=perms.org_id,
                kb_slug=kb_slug,
                # 'kb_not_found' covers both typo-in-own-org AND cross-tenant
                # probe. Spike in this event with distinct kb_slugs from a
                # single caller signals enumeration; spike with the same
                # kb_slug from one caller signals typo.
                reason="kb_not_found_or_cross_tenant",
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

    # Step 5: Resolve caller's zitadel_org_id — the S3 key prefix convention
    # used by klai-connector + klai-knowledge-ingest, matched by the read-route
    # above. Using portal_orgs.id here would create a dual prefix convention
    # (zitadel for connector-images, portal-int for user uploads) and break
    # the read-route's auth check uniformity.
    zitadel_org_id = await _resolve_zitadel_org_id(perms.org_id, db)

    # Step 6: Build the KbImage value object — single source of truth for both
    # the S3 key and the public URL. ImageStore writes by s3_key; the response
    # returns public_path. The two cannot drift because they're derived from
    # the same object (SPEC-KB-IMAGES-V2-001 REQ-1).
    try:
        kb_image = KbImage.from_bytes(
            zitadel_org_id=zitadel_org_id,
            kb_slug=kb_slug,
            data=data,
            mime=mime,
        )
    except ValueError as exc:
        # Defensive: validate_image already filtered MIMEs we accept, and the
        # kb_slug came through _get_kb_or_404 which only returns valid slugs.
        # If we land here it's a programming error, not a client error.
        logger.exception("kb_image_value_class_rejected_inputs", err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported image type",
        ) from exc

    # Step 7: Upload via the shared lib (content-addressed dedup).
    store = ImageStore(
        endpoint=settings.garage_s3_endpoint,
        access_key=settings.garage_s3_access_key,
        secret_key=settings.garage_s3_secret_key,
        bucket=settings.garage_kb_bucket,
    )
    result = await store.upload_image(zitadel_org_id, kb_slug, data, kb_image.ext)

    # SPEC-KB-IMAGES-V2-FOLLOWUPS-001: the per-upload runtime drift-check
    # between result.object_key and kb_image.s3_key is now a lib-level
    # unit test (test_image_store_build_object_key_matches_kb_image_s3_key)
    # — proves the same invariant once, no per-request cost.

    logger.info(
        "kb_image_uploaded",
        org_id=perms.org_id,
        zitadel_org_id=zitadel_org_id,
        kb_slug=kb_slug,
        kb_id=kb.id,
        size=len(data),
        object_key=result.object_key,
        deduplicated=result.deduplicated,
        mime=mime,
    )

    return {
        # The URL is sourced from KbImage.public_path — the single source of
        # truth. ImageStore.build_public_url no longer exists (REQ-4) precisely
        # to make this drift-impossible.
        "url": kb_image.public_path,
        "deduplicated": result.deduplicated,
    }
