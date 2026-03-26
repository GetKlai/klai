import asyncio
import logging
from contextlib import asynccontextmanager

import procrastinate
import procrastinate.contrib.asyncpg
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

    # Initialise Procrastinate with asyncpg connector backed by the existing pool
    from knowledge_ingest import enrichment_tasks
    async_connector = procrastinate.contrib.asyncpg.AsyncpgConnector.from_pool(pool)
    proc_app = enrichment_tasks.init_app(async_connector)
    logger.info("Procrastinate app initialised.")

    # Start Procrastinate worker in background
    async with proc_app.open_async_worker(
        queues=["enrich-interactive", "enrich-bulk"],
        install_signal_handlers=False,
    ) as worker:
        worker_task = asyncio.create_task(worker.run_async())
        logger.info("Procrastinate worker started.")

        # Start org_config NOTIFY listener in background
        listener_task = asyncio.create_task(org_config.start_listener(pool))
        logger.info("org_config NOTIFY listener started.")

        yield

        logger.info("Shutting down knowledge-ingest service.")
        worker_task.cancel()
        listener_task.cancel()
        await asyncio.gather(worker_task, listener_task, return_exceptions=True)

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
