"""
Per-KB configuration (visibility) with TTL cache and PostgreSQL NOTIFY-based eviction.

Visibility values: "public" | "internal" | "private"
Default: "internal" (org-only, no per-user restriction).

Cache TTL: 60 seconds. NOTIFY evicts specific KB immediately on config change.

SPEC-TI-003-FOLLOWUP-001 AC-1/AC-2: read/write helpers take an
asyncpg.Connection (from tenant_scoped_connection); the LISTEN/NOTIFY
``start_listener`` keeps the pool because it does not issue knowledge.*
SQL -- it pins one connection for the lifetime of the service.
"""

from __future__ import annotations

import asyncio

import asyncpg
import cachetools
import structlog

logger = structlog.get_logger()

_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=100_000, ttl=60)


def _cache_key(org_id: str, kb_slug: str) -> str:
    return f"{org_id}:{kb_slug}"


async def get_kb_visibility(conn: asyncpg.Connection, org_id: str, kb_slug: str) -> str:
    """Return the visibility for this KB. Defaults to 'internal' when not configured."""
    key = _cache_key(org_id, kb_slug)
    if key in _cache:
        return str(_cache[key])

    try:
        row = await conn.fetchrow(
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


async def set_kb_visibility(
    conn: asyncpg.Connection, org_id: str, kb_slug: str, visibility: str
) -> None:
    """Upsert KB visibility config. Evicts cache immediately."""
    await conn.execute(
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

    Pool-acquire is permitted here per SPEC-TI-003-FOLLOWUP-001 AC-2:
    LISTEN/NOTIFY does not emit SQL against knowledge.* tables.
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
            logger.exception("kb_config_listener_cleanup_failed")
        await pool.release(conn)


def _on_kb_config_changed(
    _conn: asyncpg.Connection,
    _pid: int,
    _channel: str,
    payload: str,
) -> None:
    if payload in _cache:
        del _cache[payload]
        logger.info("kb_config_cache_evicted", payload=payload)
