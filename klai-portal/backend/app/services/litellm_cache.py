"""Redis cache invalidation helpers for LiteLLM pre-call hook entries.

The LiteLLM hook caches two per-user lookups in Redis:

- the KB feature/scope preference — short-lived Redis-only version pointer
  ``kb_ver:{zitadel_org_id}:{user}`` (``_get_kb_feature``)
- prompt templates — ``templates:{zitadel_org_id}:{user}`` (``_get_templates``)

When portal-api writes change the effective state, we pre-emptively drop the
cache so the next chat request picks up the fresh state instead of waiting
out the 30-second TTL.

Single source of truth for the key SHAPES (url-shape-multi-file-drift): the
``_*_key`` / ``_*_pattern`` builders below are the ONLY portal-side place that
constructs these strings, and they MUST mirror the f-strings in
``klai_knowledge.py``. The org segment is the Zitadel org-id STRING
(``portal_orgs.zitadel_org_id``, exposed as ``UserPermissions.zitadel_org_id``),
NOT the integer ``org_id`` — the hook only ever sees the Zitadel string, so an
int-keyed DELETE silently misses and the "immediate invalidation" becomes a
no-op (only the 30s TTL saves it).

All helpers are fire-and-forget: on any Redis error they emit a
structured warning and return — callers must never depend on cache invalidation
for correctness (the feature cache has a very short TTL and never uses
LiteLLM's process-local DualCache tier).

Related: SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CACHE.

# @MX:ANCHOR: invalidate_templates is called from 4 write paths (templates
# CRUD POST/PATCH/DELETE + app_account.kb-preference PATCH) and
# invalidate_kb_cache from 2 (kb-preference PATCH + telemetry-level toggle).
# Changing their signature or key shape ripples through every caller and
# must stay in lock-step with klai_knowledge.py.
"""

from __future__ import annotations

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_TEMPLATES_KEY_PREFIX = "templates:"
_KB_VERSION_KEY_PREFIX = "kb_ver:"


def _templates_user_key(zitadel_org_id: str, librechat_user_id: str) -> str:
    return f"{_TEMPLATES_KEY_PREFIX}{zitadel_org_id}:{librechat_user_id}"


def _templates_org_pattern(zitadel_org_id: str) -> str:
    return f"{_TEMPLATES_KEY_PREFIX}{zitadel_org_id}:*"


def _kb_version_user_key(zitadel_org_id: str, librechat_user_id: str) -> str:
    return f"{_KB_VERSION_KEY_PREFIX}{zitadel_org_id}:{librechat_user_id}"


def _kb_version_org_pattern(zitadel_org_id: str) -> str:
    return f"{_KB_VERSION_KEY_PREFIX}{zitadel_org_id}:*"


async def _invalidate(
    *,
    event: str,
    zitadel_org_id: str,
    librechat_user_id: str | None,
    user_key: str,
    org_pattern: str,
) -> None:
    """Shared single/org-wide Redis DEL with uniform fire-and-forget logging.

    ``librechat_user_id`` set -> single DEL on ``user_key``.
    ``librechat_user_id`` None -> SCAN+DEL every key matching ``org_pattern``
    (CURSOR-based so a large match set never blocks Redis).
    """
    fail_event = event + "_failed"
    try:
        pool = await get_redis_pool()
    except Exception:
        logger.warning(
            fail_event,
            zitadel_org_id=zitadel_org_id,
            librechat_user_id=librechat_user_id,
            reason="redis_pool_unavailable",
            exc_info=True,
        )
        return

    if pool is None:
        # Redis not configured — no cache exists to invalidate.
        return

    if librechat_user_id is not None:
        try:
            await pool.delete(user_key)
            logger.info(event, zitadel_org_id=zitadel_org_id, librechat_user_id=librechat_user_id, mode="single")
        except Exception:
            logger.warning(
                fail_event,
                zitadel_org_id=zitadel_org_id,
                librechat_user_id=librechat_user_id,
                mode="single",
                exc_info=True,
            )
        return

    try:
        deleted = 0
        async for key in pool.scan_iter(match=org_pattern, count=100):
            await pool.delete(key)
            deleted += 1
        logger.info(event, zitadel_org_id=zitadel_org_id, mode="org-wide", deleted=deleted)
    except Exception:
        logger.warning(fail_event, zitadel_org_id=zitadel_org_id, mode="org-wide", exc_info=True)


async def invalidate_templates(
    zitadel_org_id: str,
    librechat_user_id: str | None = None,
) -> None:
    """Drop LiteLLM template cache entries (``templates:{zitadel_org_id}:...``).

    - ``librechat_user_id=None`` -> org-wide SCAN+DEL.
    - ``librechat_user_id="abc123"`` -> single DEL on the exact key.

    ``zitadel_org_id`` MUST be the Zitadel org-id string
    (``UserPermissions.zitadel_org_id``), never the int ``org_id``.
    """
    await _invalidate(
        event="templates_cache_invalidated",
        zitadel_org_id=zitadel_org_id,
        librechat_user_id=librechat_user_id,
        user_key=_templates_user_key(zitadel_org_id, librechat_user_id or ""),
        org_pattern=_templates_org_pattern(zitadel_org_id),
    )


async def invalidate_kb_cache(
    zitadel_org_id: str,
    librechat_user_id: str | None = None,
) -> None:
    """Drop the LiteLLM KB feature/scope version pointer (``kb_ver:{zitadel_org_id}:...``).

    Dropping the Redis-only version pointer forces the hook to re-fetch the
    user's KB preference from portal-api on the next chat turn; the version-keyed
    feature blob (``kb_feature:{org}:{user}:{version}``) then becomes unreachable
    and expires on its own short TTL. This intentionally does not involve
    LiteLLM DualCache because its process-local tier cannot be invalidated from
    portal-api.

    - ``librechat_user_id`` set -> single user (kb-preference PATCH).
    - ``librechat_user_id=None`` -> org-wide (telemetry-level toggle).

    ``zitadel_org_id`` MUST be the Zitadel org-id string
    (``UserPermissions.zitadel_org_id``), never the int ``org_id`` — the hook
    keys ``kb_ver:`` on the Zitadel string, so an int-keyed DELETE misses.
    """
    await _invalidate(
        event="kb_cache_invalidated",
        zitadel_org_id=zitadel_org_id,
        librechat_user_id=librechat_user_id,
        user_key=_kb_version_user_key(zitadel_org_id, librechat_user_id or ""),
        org_pattern=_kb_version_org_pattern(zitadel_org_id),
    )
