import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from knowledge_ingest import db, kb_config, org_config, qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.logging_setup import RequestContextMiddleware, setup_logging
from knowledge_ingest.middleware.auth import InternalSecretMiddleware
from knowledge_ingest.routes import (
    crawl,
    crawl_sync,
    ingest,
    internal,
    knowledge,
    personal,
    stats,
    taxonomy,
)

setup_logging("knowledge-ingest")
logger = structlog.get_logger()

# Patch graphiti-core FalkorDB search before any Graphiti usage.
# See: https://github.com/getzep/graphiti/issues/1272
# Remove once graphiti-core >= 0.29 includes the fix.
from knowledge_ingest._patch_graphiti import apply as _apply_graphiti_patch

_apply_graphiti_patch()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_knowledge_ingest_service")
    await qdrant_store.ensure_collection()
    logger.info("qdrant_collection_ready")

    pool = await db.get_pool()
    logger.info("postgres_pool_ready")

    if settings.enrichment_enabled:
        # Procrastinate worker bootstrap + zombie recovery + queue
        # subscription is owned by knowledge_ingest.worker.WorkerLifecycle.
        # Keeps the lifespan focused on lifecycle ordering, not task-runner
        # internals. See SPEC-PROCRASTINATE-ZOMBIE-001 +
        # SPEC-INGEST-QUEUE-SEPARATION-001.
        from knowledge_ingest.worker import WorkerLifecycle  # noqa: PLC0415

        async with WorkerLifecycle.start(postgres_dsn=settings.postgres_dsn):
            listener_task = asyncio.create_task(org_config.start_listener(pool))
            kb_config_listener_task = asyncio.create_task(kb_config.start_listener(pool))
            logger.info("config_listeners_started")
            try:
                yield
            finally:
                logger.info("shutting_down_config_listeners")
                listener_task.cancel()
                kb_config_listener_task.cancel()
                await asyncio.gather(
                    listener_task, kb_config_listener_task, return_exceptions=True
                )
    else:
        logger.info("enrichment_disabled_skipping_worker")
        yield

    logger.info("shutting_down_knowledge_ingest_service")
    await db.close_pool()


app = FastAPI(title="Klai Knowledge Ingest", lifespan=lifespan)
app.add_middleware(InternalSecretMiddleware)
app.add_middleware(RequestContextMiddleware)
app.include_router(ingest.router)
app.include_router(crawl.router)
app.include_router(crawl_sync.router)
app.include_router(personal.router)
app.include_router(knowledge.router)
app.include_router(stats.router)
app.include_router(taxonomy.router)
app.include_router(internal.router)


@app.get("/health")
async def health():
    """Check reachability of Qdrant, TEI, bge-m3-sparse, and FalkorDB."""
    import httpx
    from fastapi.responses import JSONResponse

    checks: dict[str, str] = {}

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

    # TEI (dense embeddings, port 7997 on gpu-01)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.tei_url}/health")
            checks["tei"] = "ok" if resp.status_code == 200 else f"status={resp.status_code}"
    except Exception as exc:
        checks["tei"] = f"error: {exc}"

    # bge-m3-sparse (sparse embeddings sidecar)
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.sparse_sidecar_url}/health")
            checks["bge_m3_sparse"] = (
                "ok" if resp.status_code == 200 else f"status={resp.status_code}"
            )
    except Exception as exc:
        checks["bge_m3_sparse"] = f"error: {exc}"

    # FalkorDB (only when Graphiti is enabled)
    # Uses TCP check — graphiti-core[falkordb] is deferred in requirements.txt (pydantic constraint)
    if settings.graphiti_enabled:
        try:
            import socket  # noqa: PLC0415

            s = socket.create_connection(
                (settings.falkordb_host, settings.falkordb_port), timeout=3.0
            )
            s.close()
            checks["falkordb"] = "ok"
        except Exception as exc:
            checks["falkordb"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", **checks},
        status_code=200 if all_ok else 503,
    )
