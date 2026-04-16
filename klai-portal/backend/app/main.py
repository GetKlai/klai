import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.api import me, signup
from app.api.admin import router as admin_router
from app.api.admin_integrations import router as admin_integrations_router
from app.api.app_account import router as app_account_router
from app.api.app_chat import router as app_chat_router
from app.api.app_gaps import router as app_gaps_router
from app.api.app_knowledge_bases import router as app_knowledge_bases_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.connectors import router as connectors_router
from app.api.groups import router as groups_router
from app.api.internal import router as internal_router
from app.api.knowledge import router as knowledge_router
from app.api.knowledge_bases import router as knowledge_bases_router
from app.api.mcp_servers import router as mcp_servers_router
from app.api.meetings import router as meetings_router
from app.api.oauth import router as oauth_router
from app.api.partner import router as partner_router
from app.api.taxonomy import router as taxonomy_router
from app.api.vitals import router as vitals_router
from app.api.webhooks import router as webhooks_router
from app.core.config import settings
from app.logging_setup import setup_logging
from app.middleware.logging_context import LoggingContextMiddleware
from app.services.bot_poller import poll_loop
from app.services.events import _pending as _event_tasks
from app.services.recording_cleanup import recording_cleanup_loop
from app.services.vexa import vexa
from app.services.zitadel import zitadel

setup_logging("portal-api")

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    import asyncio

    if settings.is_auth_dev_mode:
        # ── Dev mode: skip Zitadel, loud warnings ────────────────────────
        logger.critical(
            "╔══════════════════════════════════════════════════════════╗\n"
            "║  AUTH DEV MODE ACTIVE — authentication is BYPASSED      ║\n"
            "║  All requests authenticate as user: %s  ║\n"
            "║  NEVER enable this in production!                        ║\n"
            "╚══════════════════════════════════════════════════════════╝",
            settings.auth_dev_user_id or "(not configured)",
        )
        if not settings.auth_dev_user_id:
            logger.critical(
                "AUTH_DEV_USER_ID is not set. Set it to a zitadel_user_id that exists in the portal_users table."
            )
            raise SystemExit(1)
    else:
        # ── Production mode: validate secrets exist ──────────────────────
        missing = []
        if not settings.zitadel_pat:
            missing.append("ZITADEL_PAT")
        if not settings.sso_cookie_key:
            missing.append("SSO_COOKIE_KEY")
        if not settings.portal_secrets_key:
            missing.append("PORTAL_SECRETS_KEY")
        if not settings.encryption_key:
            missing.append("ENCRYPTION_KEY")
        if not settings.database_url:
            missing.append("DATABASE_URL")
        if missing:
            logger.critical("Missing required environment variables: %s", ", ".join(missing))
            raise SystemExit(1)
        # Validate ENCRYPTION_KEY format (REQ-CRYPTO-003)
        enc_key = settings.encryption_key
        if len(enc_key) != 64 or not all(c in "0123456789abcdefABCDEF" for c in enc_key):
            logger.critical(
                "ENCRYPTION_KEY is not a valid 64-character hex string. Generate with: openssl rand -hex 32"
            )
            raise SystemExit(1)

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

    poller_task = asyncio.create_task(poll_loop())
    logger.info("Bot poller started")

    cleanup_task = asyncio.create_task(recording_cleanup_loop())
    logger.info("Recording cleanup loop started")

    imap_task: asyncio.Task[None] | None = None
    if settings.imap_host and settings.imap_username:
        from app.services.imap_listener import start_imap_listener

        imap_task = asyncio.create_task(start_imap_listener())
        logger.info("IMAP listener started")
    else:
        logger.warning("IMAP listener disabled: missing configuration")

    yield

    poller_task.cancel()
    cleanup_task.cancel()
    if imap_task is not None:
        imap_task.cancel()
    if _event_tasks:
        await asyncio.gather(*list(_event_tasks), return_exceptions=True)
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


app.add_middleware(LoggingContextMiddleware)

app.include_router(signup.router)
app.include_router(me.router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(groups_router)
app.include_router(billing_router)
app.include_router(knowledge_router)
app.include_router(meetings_router)
app.include_router(webhooks_router)
app.include_router(internal_router)
app.include_router(knowledge_bases_router)
app.include_router(app_account_router)
app.include_router(app_chat_router)
app.include_router(app_knowledge_bases_router)
app.include_router(app_gaps_router)
app.include_router(connectors_router)
app.include_router(taxonomy_router)
app.include_router(vitals_router)
app.include_router(mcp_servers_router)
app.include_router(admin_integrations_router)
app.include_router(partner_router)
app.include_router(oauth_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
