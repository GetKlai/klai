import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from knowledge_ingest import db, org_config, qdrant_store
from knowledge_ingest.middleware.auth import InternalSecretMiddleware
from knowledge_ingest.routes import crawl, ingest, knowledge, personal, retrieve

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting knowledge-ingest service...")
    await qdrant_store.ensure_collection()
    logger.info("Qdrant collection ready.")

    pool = await db.get_pool()
    logger.info("PostgreSQL pool ready.")

    from knowledge_ingest.config import settings

    if settings.enrichment_enabled:
        # Lazy import: procrastinate pulls in psycopg which requires libpq.
        # Guarded by enrichment_enabled so test environments (ENRICHMENT_ENABLED=false)
        # skip this block entirely without needing psycopg installed.
        import procrastinate  # noqa: PLC0415
        from knowledge_ingest import enrichment_tasks  # noqa: PLC0415

        # procrastinate 2.x uses PsycopgConnector (psycopg3); no asyncpg connector exists.
        # Use SQLAlchemy make_url to safely parse the DSN (handles base64 passwords with
        # '/', '+', '=' that break stdlib urlparse), then build a libpq key=value string.
        from sqlalchemy.engine import make_url  # noqa: PLC0415
        from knowledge_ingest.config import settings as _s  # noqa: PLC0415
        _u = make_url(_s.postgres_dsn)
        # Wrap password in single quotes: base64 passwords end with '=' which
        # libpq key=value format interprets as a new separator without quoting.
        _pw = (_u.password or "").replace("\\", "\\\\").replace("'", "\\'")
        pg_dsn = (
            f"host={_u.host} port={_u.port or 5432} "
            f"dbname={_u.database} user={_u.username} password='{_pw}'"
        )
        # Pass kwargs={} to avoid psycopg-pool 3.x bug: default kwargs=None → **None TypeError.
        async_connector = procrastinate.PsycopgConnector(conninfo=pg_dsn, kwargs={})
        proc_app = enrichment_tasks.init_app(async_connector)
        logger.info("Procrastinate app initialised.")

        # procrastinate 2.x: open_async() opens the connector; run_worker_async() is the worker coroutine.
        async with proc_app.open_async():
            worker_task = asyncio.create_task(
                proc_app.run_worker_async(
                    queues=["enrich-interactive", "enrich-bulk"],
                    install_signal_handlers=False,
                )
            )
            listener_task = asyncio.create_task(org_config.start_listener(pool))
            logger.info("Procrastinate worker and org_config listener started.")

            yield

            logger.info("Shutting down knowledge-ingest service.")
            worker_task.cancel()
            listener_task.cancel()
            await asyncio.gather(worker_task, listener_task, return_exceptions=True)
    else:
        logger.info("Enrichment disabled — skipping Procrastinate worker.")
        yield
        logger.info("Shutting down knowledge-ingest service.")

    await db.close_pool()


app = FastAPI(title="Klai Knowledge Ingest", lifespan=lifespan)
app.add_middleware(InternalSecretMiddleware)
app.include_router(ingest.router)
app.include_router(retrieve.router)
app.include_router(crawl.router)
app.include_router(personal.router)
app.include_router(knowledge.router)


@app.get("/health")
async def health():
    """Check reachability of Qdrant, TEI, bge-m3-sparse, and FalkorDB."""
    import httpx
    from fastapi.responses import JSONResponse

    checks: dict[str, str] = {}

    # Qdrant
    try:
        from qdrant_client import AsyncQdrantClient

        qc = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            timeout=3.0,
        )
        await qc.get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:
        checks["qdrant"] = f"error: {exc}"

    # TEI (dense embeddings)
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
            checks["bge_m3_sparse"] = "ok" if resp.status_code == 200 else f"status={resp.status_code}"
    except Exception as exc:
        checks["bge_m3_sparse"] = f"error: {exc}"

    # FalkorDB (only when Graphiti is enabled)
    # Uses TCP check — graphiti-core[falkordb] is deferred in requirements.txt (pydantic constraint)
    if settings.graphiti_enabled:
        try:
            import socket  # noqa: PLC0415

            s = socket.create_connection((settings.falkordb_host, settings.falkordb_port), timeout=3.0)
            s.close()
            checks["falkordb"] = "ok"
        except Exception as exc:
            checks["falkordb"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", **checks},
        status_code=200 if all_ok else 503,
    )
