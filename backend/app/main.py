from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api import me, signup
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.internal import router as internal_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.services.zitadel import zitadel


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await zitadel.close()


app = FastAPI(
    title="Klai Portal API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
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
app.include_router(webhooks_router)
app.include_router(internal_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
