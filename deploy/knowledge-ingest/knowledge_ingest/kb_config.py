"""
Per-KB configuration (visibility) with TTL cache and PostgreSQL NOTIFY-based eviction.

Visibility values: "public" | "internal" | "private"
Default: "internal" (org-only, no per-user restriction).

Cache TTL: 60 seconds. NOTIFY evicts specific KB immediately on config change.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import cachetools

logger = logging.getLogger(__name__)

_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=100_000, ttl=60)


def _cache_key(org_id: str, kb_slug: str) -> str:
    return f"{org_id}:{kb_slug}"


async def get_kb_visibility(org_id: str, kb_slug: str, pool: asyncpg.Pool) -> str:
    """Return the visibility for this KB. Defaults to 'internal' when not configured."""
    key = _cache_key(org_id, kb_slug)
    if key in _cache:
        return str(_cache[key])

    try:
        row = await pool.fetchrow(
            "SELECT visibility FROM knowledge.kb_config WHERE org_id = $1 AND kb_slug = $2",
            org_id,
            kb_slug,
        )
        visibility = row["visibility"] if row else "internal"
    except Exception:
        logger.exception(
            "Failed to fetch KB visibility from DB (org=%s kb=%s), defaulting to 'internal'",
            org_id,
            kb_slug,
        )
        visibility = "internal"
    _cache[key] = visibility
    return visibility


async def set_kb_visibility(org_id: str, kb_slug: str, visibility: str, pool: asyncpg.Pool) -> None:
    """Upsert KB visibility config. Evicts cache immediately."""
    await pool.execute(
        """
        INSERT INTO knowledge.kb_config (org_id, kb_slug, visibility, updated_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (org_id, kb_slug) DO UPDATE
            SET visibility = EXCLUDED.visibility,
                updated_at = EXCLUDED.updated_at
        """,
        org_id,
        kb_slug,
        visibility,
    )
    key = _cache_key(org_id, kb_slug)
    _cache.pop(key, None)


async def start_listener(pool: asyncpg.Pool) -> None:
    """
    Listen on kb_config_changed channel.
    Evicts the specific KB from the TTL cache when its config changes.
    Runs indefinitely as a background task — cancel to stop.
    """
    conn: asyncpg.Connection = await pool.acquire()  # type: ignore[assignment]
    try:
        await conn.add_listener("kb_config_changed", _on_kb_config_changed)
        await asyncio.sleep(float("inf"))
    except asyncio.CancelledError:
        pass
    finally:
        try:
            await conn.remove_listener("kb_config_changed", _on_kb_config_changed)
        except Exception:
            pass
        await pool.release(conn)


def _on_kb_config_changed(
    _conn: asyncpg.Connection,
    _pid: int,
    _channel: str,
    payload: str,
) -> None:
    if payload in _cache:
        del _cache[payload]
        logger.info("Evicted kb_config cache for %s", payload)
