import logging
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
from app.logging_setup import RequestContextMiddleware, setup_logging
from app.middleware.auth_guard import AuthGuardMiddleware

setup_logging()
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
    from app.services import qdrant_store
    qdrant_store.ensure_collection()
    yield


app = FastAPI(
    title="Research API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Middleware registration order matters. Starlette runs middleware in REVERSE
# registration order on incoming requests (last-added = outermost / runs first).
# We want: CORS (outermost, wraps errors with CORS headers + handles preflight)
#          → RequestContextMiddleware (binds request_id / org_id to log context)
#          → AuthGuardMiddleware (rejects missing Authorization early)
#          → route handler
# So we register in reverse: AuthGuard first, then RequestContext, then CORS.
# This also ensures 401s from AuthGuard still carry CORS headers + request_id.

# SPEC-SEC-004: defense-in-depth — reject requests without Authorization header
# before any route handler runs. Token validity is still verified per-route via
# `Depends(get_current_user)`; this guard only checks *presence* and is a safety
# net if a new route forgets its auth dependency.
app.add_middleware(AuthGuardMiddleware)

app.add_middleware(RequestContextMiddleware)

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
