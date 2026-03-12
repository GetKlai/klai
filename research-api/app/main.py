import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.api.history import router as history_router
from app.api.notebooks import router as notebooks_router
from app.api.sources import router as sources_router
from app.core.config import settings

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


async def _warmup_docling() -> None:
    """Send a small warmup request to docling-serve so models are loaded before first user request."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.docling_url}/v1alpha/convert/source",
                json={"http_source": {"url": "https://example.com"}},
            )
            if resp.status_code < 500:
                logger.info("docling-serve warmup complete")
            else:
                logger.warning("docling-serve warmup returned %s", resp.status_code)
    except Exception:
        logger.warning("docling-serve warmup failed — will be ready on first request")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _warmup_docling()
    yield


app = FastAPI(
    title="Research API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+\.getklai\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(notebooks_router)
app.include_router(sources_router)
app.include_router(chat_router)
app.include_router(history_router)
