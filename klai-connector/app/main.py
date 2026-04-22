"""FastAPI application factory for klai-connector."""

import base64
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import update

import app.core.database as _db
from app.adapters.github import GitHubAdapter
from app.adapters.google_drive import GoogleDriveAdapter
from app.adapters.notion import NotionAdapter
from app.adapters.registry import AdapterRegistry
from app.adapters.webcrawler import WebCrawlerAdapter
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.config import Settings
from app.core.database import dispose_engine, init_engine
from app.core.enums import SyncStatus
from app.core.logging import RequestContextMiddleware, get_logger, setup_logging
from app.core.security import AESGCMCipher
from app.middleware.auth import AuthMiddleware
from app.models.sync_run import SyncRun
from app.routes.connectors import router as connectors_router
from app.routes.fingerprint import router as fingerprint_router
from app.routes.health import router as health_router
from app.routes.sync import router as sync_router
from app.services.crypto import PostgresSecretsStore
from app.services.portal_client import PortalClient
from app.services.scheduler import ConnectorScheduler
from app.services.sync_engine import SyncEngine

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
        if _db.session_maker is not None:
            async with _db.session_maker() as session:
                await session.execute(
                    update(SyncRun)
                    .where(SyncRun.status == SyncStatus.RUNNING)
                    .values(status=SyncStatus.PENDING, completed_at=datetime.now(UTC))
                )
                await session.commit()
            logger.info("Cleaned up stuck RUNNING sync_runs on startup")

        # Encryption
        key_bytes = base64.b64decode(settings.encryption_key)
        cipher = AESGCMCipher(key_bytes)
        secrets_store = PostgresSecretsStore(cipher)
        app.state.secrets_store = secrets_store

        # Portal client (control plane) — constructed before adapters so OAuth
        # adapters can receive it for token writeback.
        portal_client = PortalClient(settings)

        # Adapter registry
        registry = AdapterRegistry()
        registry.register("github", GitHubAdapter(settings))
        registry.register("web_crawler", WebCrawlerAdapter(settings))
        registry.register("notion", NotionAdapter(settings))
        # Google Drive adapter — only registered when OAuth client is configured.
        if settings.google_drive_client_id:
            registry.register(
                "google_drive",
                GoogleDriveAdapter(settings=settings, portal_client=portal_client),
            )
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
            from app.services.s3_storage import ImageStore

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
            image_store=image_store,
            crawl_sync_client=crawl_sync_client,
        )
        app.state.sync_engine = sync_engine

        # Scheduler
        scheduler = ConnectorScheduler()
        app.state.scheduler = scheduler
        await scheduler.start(_db.session_maker, sync_engine.run_sync)

        logger.info("klai-connector started successfully")
        yield

        # -- Shutdown --
        logger.info("Shutting down klai-connector")
        await scheduler.shutdown()
        await registry.aclose()
        await ingest_client.aclose()
        await dispose_engine()
        logger.info("klai-connector shut down")

    app = FastAPI(title="klai-connector", version="0.1.0", lifespan=lifespan)

    # CORS — allow portal frontend origin(s) to call the connector API
    allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # Auth middleware (excludes /health internally)
    app.add_middleware(AuthMiddleware, settings=settings)

    # Request context middleware (binds request_id, org_id to structlog)
    app.add_middleware(RequestContextMiddleware)

    # Routes
    app.include_router(health_router)
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")
    app.include_router(fingerprint_router, prefix="/api/v1")

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8200)  # noqa: S104  # Docker container bind, internal network only
