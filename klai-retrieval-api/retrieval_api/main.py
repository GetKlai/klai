"""retrieval-api: FastAPI service for hybrid vector search and RAG synthesis."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_client import make_asgi_app
from starlette.middleware.cors import CORSMiddleware

from retrieval_api.api.chat import router as chat_router
from retrieval_api.api.retrieve import router as retrieve_router
from retrieval_api.config import settings
from retrieval_api.logging_setup import RequestContextMiddleware, setup_logging
from retrieval_api.middleware.auth import AuthMiddleware

setup_logging()
logger = logging.getLogger(__name__)


async def _warmup_reranker() -> None:
    """Send a dummy request to load the reranker model before the first real query."""
    if not settings.reranker_enabled or not settings.infinity_reranker_url:
        return
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            await client.post(
                f"{settings.infinity_reranker_url}/v1/rerank",
                json={
                    "model": "bge-reranker-v2-m3",
                    "query": "warmup",
                    "documents": ["warmup document"],
                    "top_n": 1,
                },
            )
        logger.info("reranker warmup complete")
    except Exception as exc:
        logger.warning("reranker warmup failed (non-fatal): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from retrieval_api.services.events import close_pool, init_pool

    logger.info(
        "retrieval-api starting | qdrant=%s tei=%s litellm=%s",
        settings.qdrant_url,
        settings.tei_url,
        settings.litellm_url,
    )
    await init_pool()
    await _warmup_reranker()
    yield
    await close_pool()
    logger.info("retrieval-api shutting down")


app = FastAPI(title="retrieval-api", version="1.0.0", lifespan=lifespan)

# Middleware registration order: last-added runs FIRST on the request
# (Starlette LIFO — see .claude/rules/klai/lang/python.md and
# SPEC-SEC-CORS-001 REQ-7). Desired execution: CORS (outermost, wraps 401
# with CORS headers, handles preflight) -> RequestContext (logging) ->
# Auth (reject missing header) -> route. So we register in reverse:
# Auth, RequestContext, CORS.
#
# Deny-by-default starter — retrieval-api is on klai-net only today (no
# Caddy route), but a future browser exposure would silently inherit no
# CORS policy and immediately be cross-origin-credentialed-probable.
# SPEC-SEC-CORS-001 REQ-7 forces an explicit empty allowlist now so that
# any future Caddy exposure must update the allowlist explicitly.

# SPEC-SEC-010 REQ-1.4: AuthMiddleware first (innermost); RequestContext
# wraps it so request_id is bound before AuthMiddleware emits its first log.
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestContextMiddleware)

# CORSMiddleware registered LAST (outermost) per SPEC-SEC-CORS-001 REQ-7.
# Empty allowlist = deny-by-default; allow_credentials=False for safety.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=None,
    allow_credentials=False,
    allow_methods=[],
    allow_headers=[],
)
app.include_router(retrieve_router, prefix="")
app.include_router(chat_router, prefix="")

# Prometheus metrics endpoint — scraped by Grafana Alloy → VictoriaMetrics
app.mount("/metrics", make_asgi_app())


@app.get("/health")
async def health():
    """Check reachability of TEI, Qdrant, LiteLLM, and (optionally) FalkorDB.

    SPEC-SEC-HYGIENE-001 REQ-39: dependency failures MUST NOT leak internal
    topology (hostnames, IPs, error strings) to unauthenticated callers, and
    sync clients MUST NOT block the asyncio event loop. The exception detail
    is preserved in structured logs via ``exc_info=True``.
    """
    checks: dict[str, str] = {}

    # TEI (dense embeddings, port 7997 on gpu-01)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.tei_url}/health")
            checks["tei"] = "ok" if resp.status_code == 200 else "error"
    except Exception:
        logger.warning("health_check_failed: tei", exc_info=True)
        checks["tei"] = "error"

    # Qdrant
    try:
        import warnings

        from qdrant_client import AsyncQdrantClient

        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
            qc = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=3.0,
            )
        await qc.get_collections()
        checks["qdrant"] = "ok"
    except Exception:
        logger.warning("health_check_failed: qdrant", exc_info=True)
        checks["qdrant"] = "error"

    # LiteLLM
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            headers = {}
            if settings.litellm_api_key:
                headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
            resp = await client.get(f"{settings.litellm_url}/health/liveliness", headers=headers)
            checks["litellm"] = "ok" if resp.status_code == 200 else "error"
    except Exception:
        logger.warning("health_check_failed: litellm", exc_info=True)
        checks["litellm"] = "error"

    # FalkorDB — only checked when Graphiti is enabled (AC-12).
    # REQ-39.1: ``db.connection.ping()`` is the falkordb sync client; running
    # it directly in an async handler blocks the event loop (Caddy polls
    # /health every ~10 s). Hop into the default thread pool via
    # ``asyncio.to_thread`` so concurrent /health probes and live retrieve
    # traffic are not stalled on the ping round-trip.
    if settings.graphiti_enabled:
        try:
            from falkordb import FalkorDB

            db = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)
            await asyncio.to_thread(db.connection.ping)
            checks["falkordb"] = "ok"
        except Exception:
            logger.warning("health_check_failed: falkordb", exc_info=True)
            checks["falkordb"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", **checks},
        status_code=status_code,
    )
