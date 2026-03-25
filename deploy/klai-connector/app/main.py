"""FastAPI application factory for klai-connector."""

import base64
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.github import GitHubAdapter
from app.adapters.registry import AdapterRegistry
from app.adapters.webcrawler import WebCrawlerAdapter
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.config import Settings
import app.core.database as _db
from app.core.database import dispose_engine, init_engine
from app.core.logging import get_logger, setup_logging
from app.core.security import AESGCMCipher
from app.middleware.auth import AuthMiddleware
from app.routes.connectors import router as connectors_router
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
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage application startup and shutdown."""
        # -- Startup --
        logger.info("Starting klai-connector")

        # Database
        init_engine(settings.database_url)

        # Encryption
        key_bytes = base64.b64decode(settings.encryption_key)
        cipher = AESGCMCipher(key_bytes)
        secrets_store = PostgresSecretsStore(cipher)
        app.state.secrets_store = secrets_store

        # Adapter registry
        registry = AdapterRegistry()
        registry.register("github", GitHubAdapter(settings))
        registry.register("web_crawler", WebCrawlerAdapter(settings))
        app.state.registry = registry

        # Knowledge-ingest client
        ingest_client = KnowledgeIngestClient(settings.knowledge_ingest_url)
        app.state.ingest_client = ingest_client

        # Portal client (control plane)
        portal_client = PortalClient(settings)

        # Sync engine
        if _db.session_maker is None:
            raise RuntimeError("Database session maker not initialised")
        sync_engine = SyncEngine(
            session_maker=_db.session_maker,
            registry=registry,
            ingest_client=ingest_client,
            portal_client=portal_client,
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

    # Routes
    app.include_router(health_router)
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8200)
