"""retrieval-api: FastAPI service for hybrid vector search and RAG synthesis."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from retrieval_api.api.chat import router as chat_router
from retrieval_api.api.retrieve import router as retrieve_router
from retrieval_api.config import settings
from retrieval_api.logging_setup import RequestContextMiddleware, setup_logging

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
    logger.info(
        "retrieval-api starting | qdrant=%s tei=%s litellm=%s",
        settings.qdrant_url,
        settings.tei_url,
        settings.litellm_url,
    )
    await _warmup_reranker()
    yield
    logger.info("retrieval-api shutting down")


app = FastAPI(title="retrieval-api", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestContextMiddleware)
app.include_router(retrieve_router, prefix="")
app.include_router(chat_router, prefix="")

# Prometheus metrics endpoint — scraped by Grafana Alloy → VictoriaMetrics
app.mount("/metrics", make_asgi_app())


@app.get("/health")
async def health():
    """Check reachability of TEI, Qdrant, and LiteLLM."""
    checks: dict[str, str] = {}

    # TEI (dense embeddings, port 7997 on gpu-01)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.tei_url}/health")
            checks["tei"] = "ok" if resp.status_code == 200 else f"status={resp.status_code}"
    except Exception as exc:
        checks["tei"] = f"error: {exc}"

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
    except Exception as exc:
        checks["qdrant"] = f"error: {exc}"

    # LiteLLM
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            headers = {}
            if settings.litellm_api_key:
                headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
            resp = await client.get(f"{settings.litellm_url}/health/liveliness", headers=headers)
            checks["litellm"] = "ok" if resp.status_code == 200 else f"status={resp.status_code}"
    except Exception as exc:
        checks["litellm"] = f"error: {exc}"

    # FalkorDB — only checked when Graphiti is enabled (AC-12)
    if settings.graphiti_enabled:
        try:
            from falkordb import FalkorDB  # noqa: PLC0415

            db = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)
            db.connection.ping()
            checks["falkordb"] = "ok"
        except Exception as exc:
            checks["falkordb"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    from fastapi.responses import JSONResponse

    return JSONResponse(content={"status": "ok" if all_ok else "degraded", **checks}, status_code=status_code)
