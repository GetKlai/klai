"""Body-inspecting authorization proxy in front of docker-socket-proxy.

SPEC-SEC-DOCKER-AUTHZ-001.

Topology (each arrow is one hop):

    portal-api ──────────────────► :2375 ┐
                                          ├─ klai-docker-authz ─► docker-socket-proxy ─► /var/run/docker.sock
    runtime-api-socket-proxy ────► :2376 ┘

docker-socket-proxy stays exactly as it is. Its method+path whitelist remains the
first line of defence and is unchanged; this process adds the one check that
whitelist cannot express — what is INSIDE a `POST /containers/create` body.

Two listeners rather than one, because identity has to come from somewhere. The
Vexa runtime reaches us through a socat unix-socket bridge, so every request from
it would present the sidecar's address; a port per principal is deterministic and
needs no peer-address guessing.

Everything except container-create is streamed through untouched. The two callers
use plain request/response endpoints only — no attach, no exec (blocked
downstream anyway), no connection hijacking — so a simple forwarder is complete
for this surface rather than merely adequate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.policy import PORTAL_API, VEXA_RUNTIME, Policy, PolicyViolation, check_create

logger = structlog.get_logger()

UPSTREAM = os.getenv("DOCKER_PROXY_URL", "http://docker-socket-proxy:2375")
PORTS = {2375: PORTAL_API, 2376: VEXA_RUNTIME}

# Hop-by-hop headers must not be forwarded (RFC 9110 §7.6.1). Content-Length is
# dropped because httpx recomputes it; keeping a stale one truncates the body.
_STRIP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


def _is_container_create(request: Request) -> bool:
    """Only `POST /<version>/containers/create` carries a HostConfig.

    Docker clients prefix an API version (`/v1.43/...`) and may omit it. Matching
    on the suffix covers both without pinning a version we would then have to
    track.
    """
    if request.method != "POST":
        return False
    path = request.url.path.rstrip("/")
    return path.endswith("/containers/create")


async def _forward(request: Request, body: bytes, client: httpx.AsyncClient) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _STRIP}
    upstream = await client.request(
        request.method,
        UPSTREAM + request.url.path,
        params=dict(request.query_params),
        content=body,
        headers=headers,
    )
    out = {k: v for k, v in upstream.headers.items() if k.lower() not in _STRIP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=out)


def build_app(policy: Policy) -> Starlette:
    """One app per listener; the policy is bound at construction, not per-request."""

    async def handler(request: Request) -> Response:
        body = await request.body()

        if _is_container_create(request):
            try:
                parsed: Any = json.loads(body) if body else {}
                if not isinstance(parsed, dict):
                    raise ValueError("body is not a JSON object")
            except ValueError as exc:
                # REQ-S-001: fail closed. An unparseable create is refused rather
                # than forwarded — we cannot police what we cannot read, and a
                # forwarded unknown is exactly the hole this service exists to close.
                logger.warning(
                    "docker_authz_denied",
                    principal=policy.name,
                    path=request.url.path,
                    reason="unparseable body",
                    error=str(exc),
                )
                return JSONResponse(
                    {"message": f"container-create refused: unparseable body ({exc})"},
                    status_code=403,
                )

            try:
                check_create(parsed, policy)
            except PolicyViolation as exc:
                # REQ-E-004: baseline is zero. Every line here is either an attack
                # or a legitimate need nobody wrote down; both want a human.
                logger.error(
                    "docker_authz_denied",
                    principal=policy.name,
                    path=request.url.path,
                    reason=str(exc),
                    container_name=request.query_params.get("name"),
                )
                return JSONResponse(
                    {"message": f"container-create refused by klai-docker-authz: {exc}"},
                    status_code=403,
                )

            logger.info(
                "docker_authz_allowed",
                principal=policy.name,
                container_name=request.query_params.get("name"),
                image=parsed.get("Image"),
            )

        return await _forward(request, body, request.app.state.client)

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "principal": policy.name})

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        # One shared client per listener: connection reuse to the upstream proxy,
        # and a bounded timeout so a hung daemon surfaces as an error instead of
        # holding a provisioning request open indefinitely.
        app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        logger.info("docker_authz_started", principal=policy.name, upstream=UPSTREAM)
        try:
            yield
        finally:
            await app.state.client.aclose()

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/{path:path}", handler, methods=["GET", "POST", "PUT", "DELETE", "HEAD"]),
        ],
    )


async def _serve() -> None:
    import uvicorn

    servers = [
        uvicorn.Server(
            uvicorn.Config(
                build_app(policy),
                host="0.0.0.0",  # noqa: S104 — internal-only network, no host port published
                port=port,
                log_config=None,
                access_log=False,
            )
        )
        for port, policy in PORTS.items()
    ]
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    from app.logging_setup import setup_logging

    setup_logging("klai-docker-authz")
    asyncio.run(_serve())
