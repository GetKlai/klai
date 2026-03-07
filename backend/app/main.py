from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import me, signup
from app.api.billing import router as billing_router
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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(signup.router)
app.include_router(me.router)
app.include_router(billing_router)
app.include_router(webhooks_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
