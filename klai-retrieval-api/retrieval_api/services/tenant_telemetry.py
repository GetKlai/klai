"""SPEC-PRIVACY-QUERY-SHADOW-001 — canonical tenant-level lookup.

Retrieval-api must enforce the canonical ``portal_orgs.telemetry_level``
on every /retrieve call rather than trust the upstream-supplied
``req.telemetry_level``. The body field is treated as a *requested
upper bound* — the effective level is ``min(client_requested, canonical)``
where ``off < shadow < full``.

This closes the gap where:

- A buggy / malicious caller could send ``full`` while the tenant has
  flipped to ``off`` and still get raw query content persisted.
- The knowledge-mcp path is privacy-by-default ``shadow`` regardless
  of tenant choice — but a tenant on ``off`` would still see shadow
  rows from third-party MCP traffic. With server-side enforcement the
  canonical ``off`` wins and zero rows are written.

Cache strategy:

- 5-minute in-process TTL keyed by ``zitadel_org_id``. A tenant-flip
  has up to 5-minute propagation delay to the retrieval-api fleet —
  acceptable because flips are rare and the canonical lookup error
  case is a privacy regression (we'd over-strip on a stale ``off``,
  not under-strip).
- On cache miss + DB error, fail-OPEN to ``shadow`` (privacy-friendly
  default). Never default to ``full`` even on error.

Lookup is best-effort: a transient DB blip should not break /retrieve.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

import structlog

from retrieval_api.services.events import get_pool

logger = structlog.get_logger()

TelemetryLevel = Literal["off", "shadow", "full"]

# Total ordering used for the min-resolution.
# off < shadow < full
_LEVEL_RANK: dict[TelemetryLevel, int] = {"off": 0, "shadow": 1, "full": 2}
_RANK_TO_LEVEL: dict[int, TelemetryLevel] = {0: "off", 1: "shadow", 2: "full"}

CACHE_TTL_SECONDS = 300  # 5 minutes
_CACHE: dict[str, tuple[float, TelemetryLevel]] = {}
_CACHE_LOCK = asyncio.Lock()

_LOOKUP_SQL = "SELECT telemetry_level::text FROM portal_orgs WHERE zitadel_org_id = $1"


async def _fetch_canonical(zitadel_org_id: str) -> TelemetryLevel:
    """Fetch the org's canonical level from portal_orgs.

    Returns 'shadow' on any error (privacy-friendly default).
    """
    pool = get_pool()
    if pool is None:
        return "shadow"
    try:
        row = await pool.fetchrow(_LOOKUP_SQL, zitadel_org_id)
    except Exception:
        logger.warning(
            "tenant_telemetry_lookup_failed",
            zitadel_org_id=zitadel_org_id,
            exc_info=True,
        )
        return "shadow"
    if row is None:
        # Unknown tenant — privacy-friendly default rather than 'full'.
        return "shadow"
    level = row["telemetry_level"]
    if level not in _LEVEL_RANK:
        # Defense-in-depth: a future enum value we don't know about
        # collapses to the safest known mode.
        logger.warning(
            "tenant_telemetry_unknown_level",
            zitadel_org_id=zitadel_org_id,
            level=level,
        )
        return "shadow"
    return level  # type: ignore[return-value]


async def get_canonical_level(zitadel_org_id: str) -> TelemetryLevel:
    """Cached canonical-level lookup, 5-minute TTL.

    Multiple concurrent calls for the same org coalesce on the cache
    lock so a cold cache produces exactly one DB hit per tenant per
    TTL window.
    """
    now = time.monotonic()
    cached = _CACHE.get(zitadel_org_id)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    async with _CACHE_LOCK:
        # Re-check inside the lock — another waiter may have refreshed.
        cached = _CACHE.get(zitadel_org_id)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        level = await _fetch_canonical(zitadel_org_id)
        _CACHE[zitadel_org_id] = (time.monotonic(), level)
        return level


def resolve_effective_level(
    client_requested: TelemetryLevel,
    canonical: TelemetryLevel,
) -> TelemetryLevel:
    """Compute the effective level as min(client_requested, canonical).

    The body field is an upper bound, never an override. A caller that
    sends 'full' against a tenant on 'off' gets 'off'. A caller that
    sends 'shadow' against a tenant on 'full' stays at 'shadow' (the
    caller's stricter privacy preference wins).
    """
    rank = min(_LEVEL_RANK[client_requested], _LEVEL_RANK[canonical])
    return _RANK_TO_LEVEL[rank]


def _reset_cache_for_tests() -> None:
    """Clear the in-process cache. Tests only — not exported."""
    _CACHE.clear()
