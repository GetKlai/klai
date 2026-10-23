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
import json
import re
from typing import TYPE_CHECKING

import asyncpg
import cachetools
import structlog

from knowledge_ingest.db import get_pool

if TYPE_CHECKING:
    from knowledge_ingest.enrichment import EnrichedChunk

logger = structlog.get_logger()

_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=100_000, ttl=60)


def _cache_key(org_id: str, kb_slug: str) -> str:
    return f"{org_id}:{kb_slug}"


async def get_kb_visibility(conn: asyncpg.Connection, org_id: str, kb_slug: str) -> str:
    """Return the visibility for this KB. Defaults to 'internal' when not configured.

    Canonical personal-KB override (2026-05-27): when ``kb_slug`` matches the
    auto-provisioned per-user pattern ``personal-<zitadel_user_id>``, force
    visibility='private' regardless of what kb_config (or its absence) says.

    Why defensive here and not only at the portal side: the per-tenant
    personal-KB row in ``portal_knowledge_bases`` was created with the
    default ``visibility='internal'``, and the ingest route trusted
    kb_config verbatim. Result was a live leak where chunks ingested
    into a personal KB via web_crawler / other connectors landed with
    ``visibility=internal`` and were returned by scope=org / scope=both
    queries from OTHER users in the same org. Anchoring the rule to the
    slug pattern (which encodes ownership and never drifts) makes that
    drift impossible to recur.
    """
    if kb_slug.startswith("personal-"):
        key = _cache_key(org_id, kb_slug)
        if key not in _cache:
            _cache[key] = "private"
        return "private"

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


# KB aliases (portal_knowledge_bases.aliases): groups of names for one thing,
# edited by the KB owner in the portal. The matching below is duplicated in
# retrieval-api's search service (query expansion); the two are separate
# deployables with no shared runtime package for it.
_alias_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=10_000, ttl=60)


async def get_kb_aliases(org_id: str, kb_slug: str) -> list[list[str]]:
    """The alias groups of one KB; an unknown KB has none.

    Read from the portal's table on the shared database, like
    ``connector_state`` reads ``portal_connectors``. asyncpg hands jsonb over
    as text because the pool registers no JSON codec.
    """
    key = _cache_key(org_id, kb_slug)
    if key not in _alias_cache:
        pool = await get_pool()
        raw = await pool.fetchval(
            """
            SELECT kb.aliases FROM portal_knowledge_bases kb
            JOIN portal_orgs org ON org.id = kb.org_id
            WHERE org.zitadel_org_id = $1 AND kb.slug = $2
            """,
            org_id,
            kb_slug,
        )
        _alias_cache[key] = json.loads(raw) if raw else []
    return _alias_cache[key]


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def add_alias_names(chunks: list[EnrichedChunk], groups: list[list[str]]) -> None:
    """Give a chunk that names an alias the group's other names.

    They go into the context prefix, which the reranker reads with the chunk,
    and into the embedded text, so a question that uses another name for the
    same product still finds and ranks the chunk.
    """
    for chunk in chunks:
        names: list[str] = []
        for group in groups:
            if any(_mentions(chunk.original_text, term) for term in group):
                names += [
                    t for t in group if t not in names and not _mentions(chunk.original_text, t)
                ]
        if names:
            line = "Also known as: " + " | ".join(names)
            chunk.context_prefix = f"{chunk.context_prefix}\n{line}".strip()
            chunk.enriched_text += f"\n\n{line}"
