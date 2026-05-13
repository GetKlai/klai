"""FastAPI application factory for klai-connector."""

import asyncio
import base64
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

import app.core.database as _db
from app.adapters.airtable import AirtableAdapter
from app.adapters.confluence import ConfluenceAdapter
from app.adapters.github import GitHubAdapter
from app.adapters.google_drive import GoogleDriveAdapter
from app.adapters.ms_docs import MsDocsAdapter
from app.adapters.notion import NotionAdapter
from app.adapters.registry import AdapterRegistry
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.config import Settings
from app.core.database import cross_org_session, dispose_engine, init_engine
from app.core.enums import SyncStatus
from app.core.logging import RequestContextMiddleware, get_logger, setup_logging
from app.core.security import AESGCMCipher
from app.middleware.auth import AuthMiddleware
from app.models.sync_run import SyncRun
from app.routes.connectors import router as connectors_router
from app.routes.fingerprint import router as fingerprint_router
from app.routes.health import router as health_router
from app.routes.internal import router as internal_router
from app.routes.sync import router as sync_router
from app.services.crypto import PostgresSecretsStore
from app.services.portal_client import PortalClient
from app.services.scheduler import ConnectorScheduler
from app.services.sync_engine import SyncEngine
from app.services.sync_run_reaper import SyncRunReaper
from app.services.sync_run_resolver import SyncRunResolver

logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = Settings()  # type: ignore[call-arg]
    setup_logging(level=settings.log_level, service_name="klai-connector")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application startup and shutdown."""
        # -- Startup --
        logger.info("Starting klai-connector")

        # Database
        init_engine(settings.database_url)

        # Mark any sync_runs that were left RUNNING (e.g. from a previous crash/restart) as PENDING.
        # PENDING preserves the cursor_state (which may contain checkpoint progress) so that
        # the next sync can resume from where it left off rather than restarting from scratch.
        #
        # SPEC-CRAWLER-006: web_crawler delegated runs are NOT stuck on
        # restart — the work runs at knowledge-ingest, ``cursor_state``
        # holds ``remote_job_id``, and :class:`SyncRunResolver` finalises
        # them at read time. Resetting those to PENDING orphans them
        # (resolver only resolves RUNNING rows). Skip them here.
        if _db.session_maker is not None:
            # cross-org-by-design: startup recovery sweep must reset RUNNING
            # sync_runs across ALL tenants. A single-tenant session would miss
            # rows from other tenants and leave them stuck after a crash/restart.
            # The UPDATE writes back to rows it already owns (org_id is set on
            # each existing row), so WITH CHECK (no IS NULL branch) is satisfied.
            # SPEC-TI-002.
            async with cross_org_session() as session:
                await session.execute(
                    update(SyncRun)
                    .where(SyncRun.status == SyncStatus.RUNNING)
                    .where(
                        # RUNNING with remote_job_id => delegated, leave alone.
                        # No JSONB key, or key absent => historical inline run, reset.
                        (SyncRun.cursor_state.is_(None)) | (SyncRun.cursor_state["remote_job_id"].astext.is_(None))  # type: ignore[index]
                    )
                    .values(status=SyncStatus.PENDING, completed_at=datetime.now(UTC))
                )
                await session.commit()
            logger.info("Cleaned up stuck RUNNING sync_runs on startup (delegated runs preserved)")

        # Encryption
        key_bytes = base64.b64decode(settings.encryption_key)
        cipher = AESGCMCipher(key_bytes)
        secrets_store = PostgresSecretsStore(cipher)
        app.state.secrets_store = secrets_store

        # Portal client (control plane) — constructed before adapters so OAuth
        # adapters can receive it for token writeback.
        # Exposed on app.state for routes that need a live config fetch
        # (e.g. the post-OAuth folder picker on ms_docs).
        portal_client = PortalClient(settings)
        app.state.portal_client = portal_client

        # Adapter registry
        registry = AdapterRegistry()
        registry.register("github", GitHubAdapter(settings))
        # SPEC-CRAWLER-004 Fase D: web_crawler is handled by the delegation path
        # in sync_engine._run_web_crawler_delegation; no local adapter is registered.
        registry.register("notion", NotionAdapter(settings))
        registry.register("airtable", AirtableAdapter(settings))
        registry.register("confluence", ConfluenceAdapter(settings))
        # Google Drive adapter — only registered when OAuth client is configured.
        if settings.google_drive_client_id:
            registry.register(
                "google_drive",
                GoogleDriveAdapter(settings=settings, portal_client=portal_client),
            )
            # SPEC-KB-CONNECTORS-001 R5.x — user-facing split of Google Workspace.
            # All three aliases reuse the same GoogleDriveAdapter instance; the
            # adapter's _extract_config injects a content_types preset based on
            # connector.connector_type.
            registry.register_alias("google_docs", "google_drive", {"content_types": ["google_doc"]})
            registry.register_alias("google_sheets", "google_drive", {"content_types": ["google_sheet"]})
            registry.register_alias("google_slides", "google_drive", {"content_types": ["google_slides"]})
        else:
            logger.warning("google_drive adapter not registered — GOOGLE_DRIVE_CLIENT_ID unset")
        # Microsoft 365 adapter (SPEC-KB-MS-DOCS-001) — conditional on OAuth client.
        if settings.ms_docs_client_id:
            registry.register(
                "ms_docs",
                MsDocsAdapter(settings=settings, portal_client=portal_client),
            )
        else:
            logger.warning("ms_docs adapter not registered — MS_DOCS_CLIENT_ID unset")
        app.state.registry = registry

        # Knowledge-ingest client
        ingest_client = KnowledgeIngestClient(settings.knowledge_ingest_url, settings.knowledge_ingest_secret)
        app.state.ingest_client = ingest_client

        # SPEC-CRAWLER-004 Fase D — delegation client for web_crawler syncs.
        # Shares the same base URL + internal secret as KnowledgeIngestClient.
        from app.clients.knowledge_ingest import CrawlSyncClient

        crawl_sync_client = CrawlSyncClient(
            settings.knowledge_ingest_url,
            settings.knowledge_ingest_secret,
        )
        app.state.crawl_sync_client = crawl_sync_client

        # Image storage (Garage S3) — optional, skip if not configured.
        image_store = None
        if settings.garage_s3_endpoint:
            from klai_image_storage import ImageStore

            image_store = ImageStore(
                endpoint=settings.garage_s3_endpoint,
                access_key=settings.garage_access_key,
                secret_key=settings.garage_secret_key,
                bucket=settings.garage_bucket,
                region=settings.garage_region,
            )
            logger.info("Image storage enabled (endpoint=%s)", settings.garage_s3_endpoint)

        # Sync engine
        if _db.session_maker is None:
            raise RuntimeError("Database session maker not initialised")
        sync_engine = SyncEngine(
            session_maker=_db.session_maker,
            registry=registry,
            ingest_client=ingest_client,
            portal_client=portal_client,
            settings=settings,
            image_store=image_store,
            crawl_sync_client=crawl_sync_client,
        )
        app.state.sync_engine = sync_engine

        # SPEC-CRAWLER-006: live status resolver for delegated
        # web_crawler runs. Shares the same crawl_sync_client +
        # portal_client + session_maker so terminal state lands
        # consistently on first read after the remote job finishes.
        app.state.sync_run_resolver = SyncRunResolver(
            crawl_sync_client=crawl_sync_client,
            session_maker=_db.session_maker,
            portal_client=portal_client,
        )

        # SPEC-CRAWLER-006 REQ-06: background reaper for orphan delegated
        # sync_runs that nobody reads via the resolver-on-read path.
        reaper = SyncRunReaper(
            crawl_sync_client=crawl_sync_client,
            session_maker=_db.session_maker,
            portal_client=portal_client,
        )
        reaper_task = asyncio.create_task(reaper.async_run())
        app.state.sync_run_reaper = reaper
        app.state.sync_run_reaper_task = reaper_task

        # Scheduler
        scheduler = ConnectorScheduler()
        app.state.scheduler = scheduler
        await scheduler.start(_db.session_maker, sync_engine.run_sync)

        logger.info("klai-connector started successfully")
        yield

        # -- Shutdown --
        logger.info("Shutting down klai-connector")
        reaper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reaper_task
        await scheduler.shutdown()
        await registry.aclose()
        await ingest_client.aclose()
        await dispose_engine()
        logger.info("klai-connector shut down")

    app = FastAPI(title="klai-connector", version="0.1.0", lifespan=lifespan)

    # Middleware registration order: last-added runs FIRST on the request
    # (Starlette LIFO — see .claude/rules/klai/lang/python.md and
    # SPEC-SEC-CORS-001 REQ-6). Desired execution: CORS (outermost, wraps 401
    # with CORS headers, handles preflight) -> RequestContext (logging) ->
    # Auth (reject missing header) -> route. So we register in reverse:
    # Auth, RequestContext, CORS.

    # Auth middleware (excludes /health internally)
    app.add_middleware(AuthMiddleware, settings=settings)

    # Request context middleware (binds request_id, org_id to structlog)
    app.add_middleware(RequestContextMiddleware)

    # CORS — allow portal frontend origin(s) to call the connector API.
    # Must be registered LAST so it is the outermost layer and wraps 401
    # responses with Access-Control-Allow-Origin (SPEC-SEC-CORS-001 REQ-6.4).
    allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # Routes
    app.include_router(health_router)
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")
    app.include_router(fingerprint_router, prefix="/api/v1")
    app.include_router(internal_router)

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8200)  # noqa: S104  # Docker container bind, internal network only
