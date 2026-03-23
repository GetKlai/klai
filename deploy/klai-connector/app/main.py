"""FastAPI application factory for klai-connector."""

import base64
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.adapters.github import GitHubAdapter
from app.clients.knowledge_ingest import KnowledgeIngestClient
from app.core.config import Settings
from app.core.database import dispose_engine, init_engine
from app.core.database import session_maker as get_session_maker
from app.core.logging import get_logger, setup_logging
from app.core.security import AESGCMCipher
from app.middleware.auth import AuthMiddleware
from app.routes.connectors import router as connectors_router
from app.routes.health import router as health_router
from app.routes.sync import router as sync_router
from app.services.crypto import PostgresSecretsStore
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

        # GitHub adapter
        adapter = GitHubAdapter(settings)
        app.state.adapter = adapter

        # Knowledge-ingest client
        ingest_client = KnowledgeIngestClient(settings.knowledge_ingest_url)
        app.state.ingest_client = ingest_client

        # Sync engine
        sm = get_session_maker
        if sm is None:
            raise RuntimeError("Database session maker not initialised")
        sync_engine = SyncEngine(
            session_maker=sm,
            adapter=adapter,
            ingest_client=ingest_client,
        )
        app.state.sync_engine = sync_engine

        # Scheduler
        scheduler = ConnectorScheduler()
        app.state.scheduler = scheduler
        await scheduler.start(sm, sync_engine.run_sync)

        logger.info("klai-connector started successfully")
        yield

        # -- Shutdown --
        logger.info("Shutting down klai-connector")
        await scheduler.shutdown()
        await adapter.aclose()
        await ingest_client.aclose()
        await dispose_engine()
        logger.info("klai-connector shut down")

    app = FastAPI(title="klai-connector", version="0.1.0", lifespan=lifespan)

    # Middleware (excludes /health internally)
    app.add_middleware(AuthMiddleware, settings=settings)

    # Routes
    app.include_router(health_router)
    app.include_router(connectors_router, prefix="/api/v1")
    app.include_router(sync_router, prefix="/api/v1")

    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8200)
