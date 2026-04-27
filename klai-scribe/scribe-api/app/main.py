from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.transcribe import router as transcribe_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.logging_setup import RequestContextMiddleware, setup_logging
from app.middleware.auth_guard import AuthGuardMiddleware
from app.services.reaper import reap_stranded

setup_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # SPEC-SEC-HYGIENE-001 REQ-35.1 — reap stranded `processing` rows left
    # behind by the previous worker (OOM, container kill, crash). Best-effort:
    # an exception here MUST NOT prevent app startup, only get logged.
    #
    # When N replicas start simultaneously, all N race on the same rows.
    # SQLAlchemy retries on row-lock conflict; the net effect is correct
    # but wastes a tiny amount of work. Scribe runs single-replica today;
    # if that changes, consider an advisory lock or a leader election.
    try:
        async with AsyncSessionLocal() as session:
            count = await reap_stranded(
                session, timeout_min=settings.scribe_stranded_timeout_min
            )
        if count:
            logger.warning("scribe_startup_reaped", count=count)
    except Exception:
        logger.warning("scribe_startup_reaper_failed", exc_info=True)

    # TODO(SPEC-SEC-HYGIENE-001 REQ-36.2 follow-up): wire `janitor.sweep_orphans`
    # as a periodic task (asyncio.create_task with a 1h sleep, or a separate
    # APScheduler job) so the orphan-cleanup actually runs in production.
    # The function is currently only callable on-demand; this slice intentionally
    # skipped scheduling per the SPEC scope discussion.

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

# @MX:WARN: permissive CORS regex + allow_credentials=True
# @MX:REASON: SPEC-SEC-HYGIENE-001 REQ-38 and SPEC-SEC-CORS-001.
# This is currently safe ONLY because scribe is back-end-only and not
# browser-reachable — portal-api is the sole HTTP peer, called server-side.
# If a future UI ever issues XHR directly to scribe, the combination of a
# permissive `allow_origin_regex` and `allow_credentials=True` becomes a
# cross-origin credentialed-request vector. Before exposing scribe to
# browsers: tighten this regex to an explicit allowlist and re-run
# SPEC-SEC-CORS-001 against the scribe surface.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+\.getklai\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(transcribe_router)
