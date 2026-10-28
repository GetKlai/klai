"""klai-slack entry point: FastAPI with Bolt mounted on /slack/* (SPEC-SLACK-001 §3)."""

from __future__ import annotations

from app.logging_setup import RequestContextMiddleware, setup_logging

setup_logging()

import asyncio  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

import structlog  # noqa: E402
from fastapi import FastAPI, Request, Response  # noqa: E402
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler  # noqa: E402
from slack_sdk.oauth.state_store.sqlalchemy import AsyncSQLAlchemyOAuthStateStore  # noqa: E402

from app import db, portal_link, repo  # noqa: E402
from app.clients.portal import PortalClient  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.internal_api import router as internal_router  # noqa: E402
from app.oauth_store import OAUTH_STATES_TABLE, EncryptedInstallationStore  # noqa: E402
from app.slack_app import (  # noqa: E402
    CONNECT_PATH,
    INSTALL_PATH,
    REDIRECT_PATH,
    build_oauth_settings,
    build_slack_app,
)

logger = structlog.get_logger()

RETENTION_INTERVAL_S = 3600
OAUTH_STATE_EXPIRATION_S = 600

settings = get_settings()
engine = db.init_engine(settings.database_url)
installation_store = EncryptedInstallationStore(client_id=settings.slack_client_id, engine=engine)
state_store = AsyncSQLAlchemyOAuthStateStore(
    expiration_seconds=OAUTH_STATE_EXPIRATION_S, engine=engine, table_name=OAUTH_STATES_TABLE
)
portal = PortalClient(settings.portal_api_url, settings.portal_internal_secret)
oauth_settings = build_oauth_settings(settings, installation_store, state_store, portal)
bolt_app = build_slack_app(settings, oauth_settings, portal)
slack_handler = AsyncSlackRequestHandler(bolt_app)


async def _retention_loop() -> None:
    while True:
        try:
            async with db.session() as s:
                await repo.purge_expired(s)
        except Exception:
            logger.exception("retention_purge_failed")
        await asyncio.sleep(RETENTION_INTERVAL_S)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        task.cancel()
        await db.dispose_engine()


app = FastAPI(
    title="klai-slack", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
)
app.add_middleware(RequestContextMiddleware)
app.include_router(internal_router)


@app.post("/slack/events")
async def slack_events(request: Request) -> Response:
    return await slack_handler.handle(request)


@app.get(INSTALL_PATH)
async def slack_install(request: Request) -> Response:
    return await slack_handler.handle(request)


@app.get(CONNECT_PATH)
async def slack_connect(token: str | None = None) -> Response:
    return await portal_link.start(token, oauth_settings, settings.portal_internal_secret)


@app.get(REDIRECT_PATH)
async def slack_oauth_redirect(request: Request) -> Response:
    return await slack_handler.handle(request)
