"""
BFF proxy router (SEC-023 / F-038).

Portal-frontend is BFF-only since SPEC-AUTH-008: it sends cookies, no Bearer
token. Two internal services still expect Bearer JWT:

- ``scribe-api``   at ``scribe-api:8020``   (Scribe module)
- ``docs-app``     at ``docs-app:3010``     (klai-docs)

This router exposes each as ``/api/<slug>/*`` under portal-api. The handler
reads the BFF ``SessionContext`` from ``request.state`` and forwards the
request to the upstream with ``Authorization: Bearer <session.access_token>``
injected. Streaming is preserved for SSE chat endpoints.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.session_deps import get_session
from app.core.config import settings
from app.core.database import get_db
from app.core.session import SessionContext
from app.services.identity_verifier import verify_bff_session_identity

logger = structlog.get_logger()

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Upstream map. Each entry: (public prefix, upstream base URL). The prefix is
# stripped before forwarding so that ``/api/scribe/...`` hits
# ``http://scribe-api:8020/...``.
# ---------------------------------------------------------------------------
_UPSTREAMS: Final[dict[str, str]] = {
    "scribe": "http://scribe-api:8020",
    # klai-docs has basePath "/docs" in next.config.ts — must be included here
    # so that /api/docs/api/orgs/... resolves to /docs/api/orgs/... upstream.
    "docs": "http://docs-app:3010/docs",
}

# Hop-by-hop headers that MUST NOT be forwarded (RFC 7230 §6.1). Plus a short
# list of request-specific headers we re-compute (host, cookie, authorization).
_HOP_BY_HOP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        # Also drop these — portal-api sets them itself, frontend never sees them.
        "host",
        "cookie",
        "authorization",
        # Length is re-derived from the body stream by httpx.
        "content-length",
    }
)

# SPEC-SEC-INTERNAL-001 REQ-3.1: explicit deny-list of secret-bearing headers.
# A client-supplied value for any of these would otherwise survive the
# hop-by-hop filter above and reach scribe / docs / retrieval upstreams,
# which trust them as authenticated. The Authorization header that portal-api
# injects (REQ-3.4) covers tenant identity; these never need to come from
# the inbound request.
_SECRET_HEADER_BLOCKLIST: Final[frozenset[str]] = frozenset(
    {
        "x-internal-secret",
        "x-klai-internal-secret",
        "x-retrieval-api-internal-secret",
        "x-scribe-api-internal-secret",
    }
)

# SPEC-SEC-INTERNAL-001 REQ-3.2: forward-compatible catch-all for any
# future secret-bearing header name. Conservatively scoped to names that
# clearly signal "internal trust boundary" to avoid stripping a
# legitimate business-domain header that happens to contain ``token``.
#
# SPEC-SEC-IDENTITY-ASSERT-002 REQ-2.3: also catches the new
# ``X-Klai-Verified-*`` family so a client cannot forge an identity
# assertion. Portal-api re-injects its own values after the strip.
_SECRET_HEADER_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?i)^(x-)?(klai-internal|internal-auth|internal-token|klai-verified-)",
)

# Response headers we do NOT pass through to the client. Cookies from upstream
# must not leak into the portal origin — upstreams are behind the BFF, the
# client never sets or reads cookies on them directly.
_RESPONSE_DROP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",  # StreamingResponse computes its own
        "set-cookie",
    }
)

# Async httpx client — shared across the app lifetime. A streaming body is
# the SSE chat endpoint's lifeblood, so timeouts are generous: connect is
# short, read is long (match retrieval-api synth timeouts).
_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Lazily construct a shared AsyncClient on first request."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
            follow_redirects=False,
        )
    return _http_client


async def _close_client() -> None:
    """Called from the app lifespan shutdown hook."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


def _build_upstream_headers(
    request: Request,
    session: SessionContext,
    *,
    service: str,
    verified_user_id: str,
    verified_org_id: str,
    verified_org_slug: str,
) -> dict[str, str]:
    """Copy incoming headers minus hop-by-hop + cookies, inject identity.

    SPEC-SEC-INTERNAL-001 REQ-3:
    - Hop-by-hop + cookie + authorization (RFC 7230 + portal-injected) dropped.
    - Secret-bearing client headers stripped via ``_SECRET_HEADER_BLOCKLIST``
      and ``_SECRET_HEADER_REGEX``. An attempt to inject one is logged at
      ``info`` with ``event=proxy_header_injection_blocked`` -- the value is
      never logged.
    - The strip happens BEFORE the Authorization injection (REQ-3.4), so a
      client cannot influence the Bearer token that portal-api forwards.

    SPEC-SEC-IDENTITY-ASSERT-002 REQ-2.3:
    - ``X-Klai-Verified-*`` headers from the client are stripped (the
      regex above catches them) and portal-api re-injects them with values
      from the BFF-verified decision. Downstream services trust these only
      when accompanied by ``X-Internal-Secret``.
    """
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        lowered = k.lower()
        if lowered in _HOP_BY_HOP:
            continue
        if lowered in _SECRET_HEADER_BLOCKLIST or _SECRET_HEADER_REGEX.match(lowered):
            logger.info(
                "proxy_header_injection_blocked",
                header=lowered,
                service=service,
            )
            continue
        headers[k] = v
    headers["Authorization"] = f"Bearer {session.access_token}"
    # SPEC-SEC-IDENTITY-ASSERT-002 REQ-2.3: portal-verified identity
    # assertion. Downstream services validate these against the
    # accompanying X-Internal-Secret before trusting the values.
    headers["X-Internal-Secret"] = settings.internal_secret
    headers["X-Klai-Verified-User-Id"] = verified_user_id
    headers["X-Klai-Verified-Org-Id"] = verified_org_id
    headers["X-Klai-Verified-Org-Slug"] = verified_org_slug
    return headers


def _filter_response_headers(upstream_headers: httpx.Headers) -> list[tuple[str, str]]:
    """Select upstream response headers that are safe to forward to the client."""
    return [(k, v) for k, v in upstream_headers.items() if k.lower() not in _RESPONSE_DROP]


async def _stream_body(upstream_response: httpx.Response) -> AsyncIterator[bytes]:
    """Yield upstream response body chunks; close the upstream response on exit."""
    try:
        async for chunk in upstream_response.aiter_raw():
            yield chunk
    finally:
        await upstream_response.aclose()


async def _proxy(
    service: str,
    rest: str,
    request: Request,
    session: SessionContext,
    db: AsyncSession,
) -> StreamingResponse | JSONResponse:
    """Forward the inbound request to the configured upstream service.

    SPEC-SEC-IDENTITY-ASSERT-002 REQ-2: verify the BFF session's identity
    (user is still an active member of session.org_id) BEFORE forwarding.
    On deny return 403 immediately; the request body is never streamed
    upstream. On allow inject ``X-Klai-Verified-*`` headers so downstream
    services can act on portal-verified identity without re-verifying.
    """
    base_url = _UPSTREAMS.get(service)
    if base_url is None:
        # This is a programming error — the route-decorator only permits known
        # services — but guard defensively anyway.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown upstream service",
        )

    path = f"/{rest}" if rest else "/"

    # SPEC-SEC-IDENTITY-ASSERT-002 REQ-2.1 + REQ-2.6: verify BEFORE the
    # request body is read or streamed upstream. A denied call must not
    # consume the upstream's request budget.
    if session.org_id is None:
        # The BFF session was created without a resolved org. Treat as a
        # session in an inconsistent state — never reachable on the happy
        # path because login flows assign org_id before issuing the cookie.
        logger.warning(
            "bff_proxy_session_without_org",
            service=service,
            path=path,
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": "identity_verification_failed",
                "reason": "no_membership",
            },
        )

    verify_started = time.monotonic()
    decision = await verify_bff_session_identity(
        db=db,
        zitadel_user_id=session.zitadel_user_id,
        portal_org_id=session.org_id,
    )
    verify_latency_ms = round((time.monotonic() - verify_started) * 1000.0, 2)

    if not decision.verified:
        logger.info(
            "bff_proxy_verified",
            service=service,
            method=request.method,
            path=path,
            verified=False,
            reason=decision.reason,
            verify_latency_ms=verify_latency_ms,
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": "identity_verification_failed",
                "reason": decision.reason or "unknown",
            },
        )

    # decision.verified is True → user_id, org_id, org_slug all populated
    # (VerifyDecision.allow factory contract). Use type narrowing via assert.
    assert decision.user_id is not None
    assert decision.org_id is not None
    assert decision.org_slug is not None

    # Build upstream URL: base + "/" + tail + original query string.
    # FastAPI strips trailing query params; re-add them from request.url.
    query = urlencode(list(request.query_params.multi_items()))
    upstream_url = f"{base_url}{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    headers = _build_upstream_headers(
        request,
        session,
        service=service,
        verified_user_id=decision.user_id,
        verified_org_id=decision.org_id,
        verified_org_slug=decision.org_slug,
    )
    body = await request.body()

    client = _get_client()

    try:
        req = client.build_request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
        )
        upstream = await client.send(req, stream=True)
    except httpx.ConnectError as exc:
        logger.warning(
            "bff_proxy_connect_failed",
            service=service,
            path=path,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream unreachable",
        ) from exc
    except httpx.TimeoutException as exc:
        logger.warning(
            "bff_proxy_timeout",
            service=service,
            path=path,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Upstream timeout",
        ) from exc

    logger.info(
        "bff_proxy_verified",
        service=service,
        method=request.method,
        path=path,
        verified=True,
        evidence=decision.evidence,
        verify_latency_ms=verify_latency_ms,
    )
    logger.info(
        "bff_proxy_forwarded",
        service=service,
        method=request.method,
        path=path,
        status=upstream.status_code,
    )

    return StreamingResponse(
        _stream_body(upstream),
        status_code=upstream.status_code,
        headers=dict(_filter_response_headers(upstream.headers)),
        media_type=upstream.headers.get("content-type"),
    )


# ---------------------------------------------------------------------------
# Route definitions — one per service. The router is mounted at /api so the
# full public paths are /api/scribe/*, /api/docs/*.
#
# FastAPI's ``api_route`` supports all methods on one handler. Tail matcher
# ``{rest:path}`` accepts arbitrary sub-paths.
# ---------------------------------------------------------------------------

_ALLOWED_METHODS: Final[list[str]] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@router.api_route("/scribe/{rest:path}", methods=_ALLOWED_METHODS, response_model=None)
async def proxy_scribe(
    rest: str,
    request: Request,
    session: SessionContext = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse | JSONResponse:
    """Forward /api/scribe/* to scribe-api:8020."""
    return await _proxy("scribe", rest, request, session, db)


@router.api_route("/docs/{rest:path}", methods=_ALLOWED_METHODS, response_model=None)
async def proxy_docs(
    rest: str,
    request: Request,
    session: SessionContext = Depends(get_session),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse | JSONResponse:
    """Forward /api/docs/* to docs-app:3010."""
    return await _proxy("docs", rest, request, session, db)


# ---------------------------------------------------------------------------
# Lifespan hook — wire this from app.main.lifespan to close the shared client
# on shutdown.
# ---------------------------------------------------------------------------


async def aclose() -> None:
    """Close the shared httpx.AsyncClient (called from app.main lifespan)."""
    await _close_client()
