"""
BFF proxy router (SEC-023 / F-038).

Portal-frontend is BFF-only since SPEC-AUTH-008: it sends cookies, no Bearer
token. Three internal services still expect Bearer JWT:

- ``research-api`` at ``research-api:8030`` (Focus module)
- ``scribe-api``   at ``scribe-api:8020``   (Scribe module)
- ``docs-app``     at ``docs-app:3010``     (klai-docs)

This router exposes each as ``/api/<slug>/*`` under portal-api. The handler
reads the BFF ``SessionContext`` from ``request.state`` and forwards the
request to the upstream with ``Authorization: Bearer <session.access_token>``
injected. Streaming is preserved for SSE chat endpoints.

After this lands, the Caddy public routes (``/research/*`` etc.) are dropped
— the upstreams become internal-only on the Docker network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.session_deps import get_session
from app.core.session import SessionContext

logger = structlog.get_logger()

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Upstream map. Each entry: (public prefix, upstream base URL). The prefix is
# stripped before forwarding so that ``/api/research/v1/notebooks`` hits
# ``http://research-api:8030/v1/notebooks``.
# ---------------------------------------------------------------------------
_UPSTREAMS: Final[dict[str, str]] = {
    "research": "http://research-api:8030",
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


def _build_upstream_headers(request: Request, session: SessionContext) -> dict[str, str]:
    """Copy incoming headers minus hop-by-hop + cookies, inject Bearer."""
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        headers[k] = v
    headers["Authorization"] = f"Bearer {session.access_token}"
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
) -> StreamingResponse:
    """Forward the inbound request to the configured upstream service."""
    base_url = _UPSTREAMS.get(service)
    if base_url is None:
        # This is a programming error — the route-decorator only permits known
        # services — but guard defensively anyway.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown upstream service",
        )

    # Build upstream URL: base + "/" + tail + original query string.
    # FastAPI strips trailing query params; re-add them from request.url.
    path = f"/{rest}" if rest else "/"
    query = urlencode(list(request.query_params.multi_items()))
    upstream_url = f"{base_url}{path}"
    if query:
        upstream_url = f"{upstream_url}?{query}"

    headers = _build_upstream_headers(request, session)
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
# full public paths are /api/research/*, /api/scribe/*, /api/docs/*.
#
# FastAPI's ``api_route`` supports all methods on one handler. Tail matcher
# ``{rest:path}`` accepts arbitrary sub-paths.
# ---------------------------------------------------------------------------

_ALLOWED_METHODS: Final[list[str]] = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


@router.api_route("/research/{rest:path}", methods=_ALLOWED_METHODS)
async def proxy_research(
    rest: str,
    request: Request,
    session: SessionContext = Depends(get_session),
) -> StreamingResponse:
    """Forward /api/research/* to research-api:8030."""
    return await _proxy("research", rest, request, session)


@router.api_route("/scribe/{rest:path}", methods=_ALLOWED_METHODS)
async def proxy_scribe(
    rest: str,
    request: Request,
    session: SessionContext = Depends(get_session),
) -> StreamingResponse:
    """Forward /api/scribe/* to scribe-api:8020."""
    return await _proxy("scribe", rest, request, session)


@router.api_route("/docs/{rest:path}", methods=_ALLOWED_METHODS)
async def proxy_docs(
    rest: str,
    request: Request,
    session: SessionContext = Depends(get_session),
) -> StreamingResponse:
    """Forward /api/docs/* to docs-app:3010."""
    return await _proxy("docs", rest, request, session)


# ---------------------------------------------------------------------------
# Lifespan hook — wire this from app.main.lifespan to close the shared client
# on shutdown.
# ---------------------------------------------------------------------------


async def aclose() -> None:
    """Close the shared httpx.AsyncClient (called from app.main lifespan)."""
    await _close_client()
