"""Redis cache invalidation helpers for LiteLLM pre-call hook entries.

The LiteLLM hook caches per-user guardrail-relevant lookups (currently
templates; guardrail rules land in a follow-up SPEC). When portal-api
writes change the effective state, we pre-emptively drop the cache so
the next chat request picks up the fresh state instead of waiting out
the 30-second TTL.

All helpers are fire-and-forget: on any Redis error they emit a
structured warning and return — callers must never depend on cache
invalidation for correctness (30s TTL is the fallback).

Related: SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-CACHE.

# @MX:ANCHOR: invalidate_templates is called from 4 write paths (templates
# CRUD POST/PATCH/DELETE + app_account.kb-preference PATCH). Changing its
# signature or behaviour ripples through all four.
"""

from __future__ import annotations

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_TEMPLATES_KEY_PREFIX = "templates:"


def _user_key(org_id: int, librechat_user_id: str) -> str:
    return f"{_TEMPLATES_KEY_PREFIX}{org_id}:{librechat_user_id}"


def _org_pattern(org_id: int) -> str:
    return f"{_TEMPLATES_KEY_PREFIX}{org_id}:*"


async def invalidate_templates(
    org_id: int,
    librechat_user_id: str | None = None,
) -> None:
    """Drop LiteLLM template cache entries.

    - ``librechat_user_id=None`` means an org-wide change (e.g. an org-scope
      template was created/updated/deleted): SCAN+DEL every key matching
      ``templates:{org_id}:*``.
    - ``librechat_user_id="abc123"`` means a user-specific change
      (e.g. active_template_ids changed, or a personal-scope template
      belonging to that user changed): single DEL on the exact key.

    Fire-and-forget: any Redis error is swallowed and logged as
    ``templates_cache_invalidation_failed``. The LiteLLM hook's 30-second
    TTL absorbs the staleness.
    """
    try:
        pool = await get_redis_pool()
    except Exception:
        logger.warning(
            "templates_cache_invalidation_failed",
            org_id=org_id,
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
            await pool.delete(_user_key(org_id, librechat_user_id))
            logger.info(
                "templates_cache_invalidated",
                org_id=org_id,
                librechat_user_id=librechat_user_id,
                mode="single",
            )
        except Exception:
            logger.warning(
                "templates_cache_invalidation_failed",
                org_id=org_id,
                librechat_user_id=librechat_user_id,
                mode="single",
                exc_info=True,
            )
        return

    # Org-wide: SCAN+DEL. We iterate in chunks so a pattern with thousands
    # of matches never blocks Redis long enough to matter (SCAN is O(1)
    # per step, CURSOR-based).
    try:
        deleted = 0
        async for key in pool.scan_iter(match=_org_pattern(org_id), count=100):
            await pool.delete(key)
            deleted += 1
        logger.info(
            "templates_cache_invalidated",
            org_id=org_id,
            mode="org-wide",
            deleted=deleted,
        )
    except Exception:
        logger.warning(
            "templates_cache_invalidation_failed",
            org_id=org_id,
            mode="org-wide",
            exc_info=True,
        )
