import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api import me, signup
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.internal import router as internal_router
from app.api.knowledge import router as knowledge_router
from app.api.meetings import router as meetings_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.services.vexa import vexa
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Validate the Zitadel PAT before accepting traffic.
    # A wrong PAT makes ALL auth endpoints fail with 401, so crash early.
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{settings.zitadel_base_url}/auth/v1/users/me",
            headers={"Authorization": f"Bearer {settings.zitadel_pat}"},
        )
    if resp.status_code != 200:
        logger.critical(
            "ZITADEL_PAT validation failed (HTTP %s). "
            "The PAT in the environment is invalid or corrupted. "
            "Fix PORTAL_API_ZITADEL_PAT in .env and restart.",
            resp.status_code,
        )
        raise SystemExit(1)
    logger.info("Zitadel PAT validated successfully")

    yield
    await vexa.close()
    await zitadel.close()


app = FastAPI(
    title="Klai Portal API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.debug else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_authenticated(request: Request, call_next: object) -> Response:
    """Prevent caching of all API responses (they are user-specific)."""
    response: Response = await call_next(request)  # type: ignore[arg-type]
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(signup.router)
app.include_router(me.router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(billing_router)
app.include_router(knowledge_router)
app.include_router(meetings_router)
app.include_router(webhooks_router)
app.include_router(internal_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
