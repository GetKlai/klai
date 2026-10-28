"""Async engine and session factory for the klai_slack database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

engine: AsyncEngine | None = None
session_maker: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> AsyncEngine:
    global engine, session_maker
    engine = create_async_engine(database_url, pool_size=5, max_overflow=10, pool_pre_ping=True)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


async def dispose_engine() -> None:
    global engine, session_maker
    if engine is not None:
        await engine.dispose()
    engine = None
    session_maker = None


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """One transaction: commits on success, rolls back on any exception."""
    if session_maker is None:
        raise RuntimeError("Database engine not initialised; call init_engine() first.")
    async with session_maker() as s, s.begin():
        yield s
