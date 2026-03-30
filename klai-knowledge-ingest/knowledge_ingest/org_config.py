"""
Per-org enrichment configuration with TTL cache and PostgreSQL NOTIFY-based eviction.

Global kill switch: ENRICHMENT_ENABLED env var (settings.enrichment_enabled).
Per-org override: knowledge.org_config table. NULL = use global default (enabled).
Cache TTL: 60 seconds. NOTIFY evicts specific org immediately on config change.
"""
import asyncio
import logging

import asyncpg
import cachetools

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)

_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=20_000, ttl=60)


async def is_enrichment_enabled(org_id: str, pool: asyncpg.Pool) -> bool:
    """Check if enrichment is enabled for this org. Global kill switch takes priority."""
    if not settings.enrichment_enabled:
        return False

    if org_id in _cache:
        return bool(_cache[org_id])

    row = await pool.fetchrow(
        "SELECT enrichment_enabled FROM knowledge.org_config WHERE org_id = $1",
        org_id,
    )
    enabled = (
        row["enrichment_enabled"]
        if row and row["enrichment_enabled"] is not None
        else True
    )
    _cache[org_id] = enabled
    return enabled


async def start_listener(pool: asyncpg.Pool) -> None:
    """
    Listen on org_config_changed channel.
    Evicts the specific org from the TTL cache when its config changes.
    Runs indefinitely as a background task — cancel to stop.
    """
    conn: asyncpg.Connection = await pool.acquire()  # type: ignore[assignment]
    try:
        await conn.add_listener("org_config_changed", _on_org_config_changed)
        await asyncio.sleep(float("inf"))
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await conn.remove_listener("org_config_changed", _on_org_config_changed)
        except Exception:
            pass
        await pool.release(conn)


def _on_org_config_changed(
    _conn: asyncpg.Connection,
    _pid: int,
    _channel: str,
    payload: str,
) -> None:
    org_id = payload
    if org_id in _cache:
        del _cache[org_id]
        logger.info("Evicted org_config cache for org_id=%s", org_id)
