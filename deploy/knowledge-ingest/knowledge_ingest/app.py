import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from knowledge_ingest import db, org_config, qdrant_store
from knowledge_ingest.middleware.auth import InternalSecretMiddleware
from knowledge_ingest.routes import crawl, ingest, personal, retrieve

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
        import procrastinate.contrib.asyncpg as _asyncpg_contrib  # noqa: PLC0415
        from knowledge_ingest import enrichment_tasks  # noqa: PLC0415

        async_connector = _asyncpg_contrib.AsyncpgConnector.from_pool(pool)
        proc_app = enrichment_tasks.init_app(async_connector)
        logger.info("Procrastinate app initialised.")

        async with proc_app.open_async_worker(
            queues=["enrich-interactive", "enrich-bulk"],
            install_signal_handlers=False,
        ) as worker:
            worker_task = asyncio.create_task(worker.run_async())
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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
