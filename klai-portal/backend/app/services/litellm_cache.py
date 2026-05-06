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

B-5 (SPEC-TI-010B): The LiteLLM hook (deploy/litellm/klai_knowledge.py)
writes cache keys using the Zitadel org_id string
(e.g. "362757920133283846"), NOT the portal_orgs integer PK.
The invalidator must use the same namespace or deletions silently miss.

# @MX:ANCHOR: invalidate_templates is called from 4 write paths (templates
# CRUD POST/PATCH/DELETE + app_account.kb-preference PATCH). Changing its
# signature or behaviour ripples through all four.
# @MX:REASON: zitadel_org_id (str) must match the writer-side key format
#   used by deploy/litellm/klai_knowledge.py. Using org.id (int) produces
#   a different namespace and the invalidation silently misses for ~30s.
"""

from __future__ import annotations

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_TEMPLATES_KEY_PREFIX = "templates:"


def _user_key(zitadel_org_id: str, librechat_user_id: str) -> str:
    return f"{_TEMPLATES_KEY_PREFIX}{zitadel_org_id}:{librechat_user_id}"


def _org_pattern(zitadel_org_id: str) -> str:
    return f"{_TEMPLATES_KEY_PREFIX}{zitadel_org_id}:*"


async def invalidate_templates(
    zitadel_org_id: str,
    librechat_user_id: str | None = None,
) -> None:
    """Drop LiteLLM template cache entries.

    - ``librechat_user_id=None`` means an org-wide change (e.g. an org-scope
      template was created/updated/deleted): SCAN+DEL every key matching
      ``templates:{zitadel_org_id}:*``.
    - ``librechat_user_id="abc123"`` means a user-specific change
      (e.g. active_template_ids changed, or a personal-scope template
      belonging to that user changed): single DEL on the exact key.

    ``zitadel_org_id`` is the Zitadel resourceowner string
    (e.g. "362757920133283846") — NOT the portal_orgs integer PK.
    This matches the writer-side key used by the LiteLLM hook
    (deploy/litellm/klai_knowledge.py::_fetch_templates).

    Fire-and-forget: any Redis error is swallowed and logged as
    ``templates_cache_invalidation_failed``. The LiteLLM hook's 30-second
    TTL absorbs the staleness.
    """
    try:
        pool = await get_redis_pool()
    except Exception:
        logger.warning(
            "templates_cache_invalidation_failed",
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
            await pool.delete(_user_key(zitadel_org_id, librechat_user_id))
            logger.info(
                "templates_cache_invalidated",
                zitadel_org_id=zitadel_org_id,
                librechat_user_id=librechat_user_id,
                mode="single",
            )
        except Exception:
            logger.warning(
                "templates_cache_invalidation_failed",
                zitadel_org_id=zitadel_org_id,
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
        async for key in pool.scan_iter(match=_org_pattern(zitadel_org_id), count=100):
            await pool.delete(key)
            deleted += 1
        logger.info(
            "templates_cache_invalidated",
            zitadel_org_id=zitadel_org_id,
            mode="org-wide",
            deleted=deleted,
        )
    except Exception:
        logger.warning(
            "templates_cache_invalidation_failed",
            zitadel_org_id=zitadel_org_id,
            mode="org-wide",
            exc_info=True,
        )
