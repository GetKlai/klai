from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.transcribe import router as transcribe_router
from app.logging_setup import RequestContextMiddleware, setup_logging
from app.middleware.auth_guard import AuthGuardMiddleware

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield


app = FastAPI(
    title="Scribe API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Middleware registration order: last-added runs FIRST on the request.
# Desired request flow: CORS (outermost, wraps 401 with CORS headers, handles
# preflight) → RequestContext (logging) → AuthGuard (reject missing header) →
# route. So we register in reverse: AuthGuard, RequestContext, CORS.

# SPEC-SEC-004: defense-in-depth — reject requests without Authorization header
# before any route handler. Token validity is checked per-route via
# `Depends(get_current_user_id)`; this guard only checks *presence* and is a
# safety net if a new route forgets its auth dependency.
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
app.include_router(transcribe_router)
