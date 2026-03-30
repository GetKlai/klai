"""
asyncpg connection pool for knowledge-ingest.

Uses SQLAlchemy URL parsing to safely extract credentials — avoids broken URL
parsing when the password contains special chars like +, /, =.
"""
import asyncpg
from sqlalchemy.engine.url import make_url

from knowledge_ingest.config import settings

_pool: asyncpg.Pool | None = None


def _parse_dsn(dsn: str) -> dict:
    """Extract asyncpg keyword args from a SQLAlchemy DSN string."""
    url = make_url(dsn)
    return {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "database": url.database,
    }


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        kwargs = _parse_dsn(settings.postgres_dsn)
        _pool = await asyncpg.create_pool(**kwargs, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
