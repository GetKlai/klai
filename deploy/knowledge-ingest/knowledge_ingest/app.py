import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from knowledge_ingest import db, qdrant_store
from knowledge_ingest.middleware.auth import InternalSecretMiddleware
from knowledge_ingest.routes import crawl, ingest, retrieve

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting knowledge-ingest service...")
    await qdrant_store.ensure_collection()
    logger.info("Qdrant collection ready.")
    await db.get_pool()
    logger.info("PostgreSQL pool ready.")
    yield
    logger.info("Shutting down knowledge-ingest service.")
    await db.close_pool()


app = FastAPI(title="Klai Knowledge Ingest", lifespan=lifespan)
app.add_middleware(InternalSecretMiddleware)
app.include_router(ingest.router)
app.include_router(retrieve.router)
app.include_router(crawl.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
