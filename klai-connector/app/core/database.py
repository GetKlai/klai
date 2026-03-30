"""Async database engine and session factory for klai-connector."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Module-level references initialised during app lifespan.
engine: AsyncEngine | None = None
session_maker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    """Create the async engine and session factory.

    Must be called once at application startup.

    Args:
        database_url: PostgreSQL connection string (asyncpg driver).

    Returns:
        The newly created ``AsyncEngine``.
    """
    global engine, session_maker  # noqa: PLW0603
    engine = create_async_engine(database_url, echo=False, pool_size=10, max_overflow=20)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def dispose_engine() -> None:
    """Dispose the engine connection pool. Call at shutdown."""
    global engine, session_maker  # noqa: PLW0603
    if engine is not None:
        await engine.dispose()
        engine = None
        session_maker = None


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    if session_maker is None:
        raise RuntimeError("Database engine not initialised. Call init_engine() first.")
    async with session_maker() as session:
        yield session
