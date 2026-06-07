"""Portal-api / retrieval-api HTTP I/O adapters for the Klai LiteLLM hook.

Every outbound service call the KB-chat hook makes — the retrieval-api
``/retrieve`` POST and the two portal-api fetches (KB feature/scope and prompt
templates) — extracted from ``klai_knowledge.py`` into one cohesive I/O module
so the orchestrator is left with policy/assembly, not transport.

These functions own their fail-open / fail-closed conventions and the Redis
cache-key shapes (``kb_ver`` / ``kb_feature`` / ``kb_feature_latest`` /
``templates``), which are an implicit cross-file contract mirrored in
klai-portal's ``app/services/litellm_cache.py`` — the literal f-string shapes
are kept verbatim (pitfall url-shape-multi-file-drift). The
``X-Caller-Service: litellm`` header is likewise contract-bearing
(SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2) and preserved byte-for-byte.

Env constants are read at import (identical timing to the previous in-module
constants); the module is registered in
``tests/klai_module_reset.KLAI_KB_MODULES`` so reloads re-read them.
``klai_knowledge`` re-imports ``retrieve`` / ``get_kb_feature`` /
``get_templates`` (aliased to their ``_``-prefixed names) so the hook call
sites are unchanged; ``retrieve_headers`` stays an internal detail.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KNOWLEDGE_RETRIEVE_URL = os.getenv("KNOWLEDGE_RETRIEVE_URL")
if not KNOWLEDGE_RETRIEVE_URL:
    raise RuntimeError("KNOWLEDGE_RETRIEVE_URL is not set")
PORTAL_API_URL = os.getenv("PORTAL_API_URL", "http://portal-api:8000")

# PORTAL_INTERNAL_SECRET authenticates calls to portal-api (entitlement check
# at /internal/v1/kb/feature, template fetch at /internal/templates/effective).
# Maps to PORTAL_API_INTERNAL_SECRET in SOPS / portal-api validates with that.
PORTAL_INTERNAL_SECRET = os.getenv("PORTAL_INTERNAL_SECRET", "")

# RETRIEVAL_INTERNAL_SECRET authenticates the /retrieve call on retrieval-api.
# retrieval-api validates against its own INTERNAL_SECRET env var (mapped from
# RETRIEVAL_API_INTERNAL_SECRET in SOPS) — a DIFFERENT secret from portal-api's.
# When unset (e.g. older deploys), falls back to PORTAL_INTERNAL_SECRET so the
# hook keeps shipping headers, but in production both secrets must be set or
# the legacy auth path on /retrieve 401s with `invalid_internal_secret`.
RETRIEVAL_INTERNAL_SECRET = (
    os.getenv("RETRIEVAL_INTERNAL_SECRET", "") or PORTAL_INTERNAL_SECRET
)

# SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-HOOK-U2: prompt-template fetch config.
PORTAL_TEMPLATES_URL = os.getenv(
    "PORTAL_TEMPLATES_URL", f"{PORTAL_API_URL}/internal/templates/effective"
)
TEMPLATES_TIMEOUT = float(os.getenv("TEMPLATES_TIMEOUT", "2.0"))


def retrieve_headers() -> dict[str, str]:
    """Internal-secret + caller-service headers for the /retrieve call.

    Uses RETRIEVAL_INTERNAL_SECRET (which falls back to PORTAL_INTERNAL_SECRET
    when unset). retrieval-api validates against its own INTERNAL_SECRET env
    var which is sourced from RETRIEVAL_API_INTERNAL_SECRET in SOPS — that is
    a DIFFERENT secret from portal-api's. Sending portal-api's secret here is
    the bug that historically caused `invalid_internal_secret` rejections on
    every retrieve call when the two secrets diverged.

    SPEC-SEC-IDENTITY-ASSERT-001 REQ-4.2: ``X-Caller-Service: litellm`` is
    REQUIRED — without it retrieval-api returns HTTP 400
    ``missing_caller_service`` and the hook silently degrades chat to
    "no KB". This was the regression that broke production for ~7 days
    after Phase D landed on 2026-04-28.
    """
    if not RETRIEVAL_INTERNAL_SECRET:
        return {}
    return {
        "X-Internal-Secret": RETRIEVAL_INTERNAL_SECRET,
        "X-Caller-Service": "litellm",
    }


async def retrieve(http: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    """POST to retrieval-api ``/retrieve`` with internal-secret auth.

    Klai authenticates service-to-service calls with a shared
    ``X-Internal-Secret`` + an ``X-Caller-Service`` header; the end-user
    identity in the body is verified by retrieval-api against portal
    ``/internal/identity/verify``. SPEC-SEC-SERVICE-AUTH-002 explored a
    per-service Zitadel client_credentials JWT here but it was dropped as
    disproportionate for the internal mesh — the receiver still accepts a
    Bearer JWT, no caller mints one.
    """
    return await http.post(
        KNOWLEDGE_RETRIEVE_URL, json=body, headers=retrieve_headers()
    )


async def get_kb_feature(user_id: str, org_id: str, cache) -> dict:
    """Return the user's KB feature state including entitlement and scope preference.

    Two-level cache strategy:
    - Version pointer (kb_ver:...) — 30s TTL. Expires when kb_pref_version increments,
      forcing a fresh portal fetch within 30s of a preference change.
    - Feature data (kb_feature:...:version) — 300s TTL.

    Fail-closed for entitlement: portal errors return enabled=False.
    Fail-open for retrieval preference: portal errors leave kb_retrieval_enabled=True
    so existing retrieval behavior is preserved (REQ-N1).

    Backward compatible: handles old {"enabled": bool} portal responses gracefully.
    """
    if not PORTAL_INTERNAL_SECRET:
        # We cannot reach portal-api — but a recent successful fetch may still
        # be cached (kb_feature_latest, 24h). Prefer the user's last-known
        # settings (which preserve their Strict/Open mode) over a hard refusal,
        # exactly like the portal-fetch-failure path below. Only refuse when
        # there is genuinely nothing cached to fall back on.
        stale = await cache.async_get_cache(f"kb_feature_latest:{org_id}:{user_id}")
        if isinstance(stale, dict):
            logger.warning(
                "KlaiKnowledgeHook: PORTAL_INTERNAL_SECRET not set — using stale "
                "feature cache"
            )
            return stale
        logger.warning(
            "KlaiKnowledgeHook: PORTAL_INTERNAL_SECRET not set and no cached "
            "settings — fail-closed"
        )
        return {
            "enabled": False,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "kb_narrow": False,
            "version": 0,
            "zitadel_user_id": None,
            # No portal secret AND no cached settings: we cannot know the user's
            # mode. The hook refuses honestly rather than silently defaulting to
            # a general answer (which would break a Strict user's KB-only promise).
            "settings_unavailable": True,
            # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4: fail-open to 'shadow', never 'off'.
            "telemetry_level": "shadow",
        }

    # Step 1: check version pointer (short-lived — invalidated by preference changes)
    version_key = f"kb_ver:{org_id}:{user_id}"
    cached_version = await cache.async_get_cache(version_key)

    if cached_version is not None:
        feature_key = f"kb_feature:{org_id}:{user_id}:{cached_version}"
        cached = await cache.async_get_cache(feature_key)
        if cached is not None:
            return cached

    latest_feature_key = f"kb_feature_latest:{org_id}:{user_id}"

    # Cache miss — fetch fresh from portal
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(
                f"{PORTAL_API_URL}/internal/v1/users/{user_id}/feature/knowledge",
                params={"org_id": org_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        stale = await cache.async_get_cache(latest_feature_key)
        if isinstance(stale, dict):
            logger.warning(
                "KlaiKnowledgeHook: portal feature fetch failed (%s) — using stale feature cache",
                exc,
            )
            return stale
        logger.warning(
            "KlaiKnowledgeHook: portal feature fetch failed (%s) — fail-closed", exc
        )
        return {
            "enabled": False,
            "kb_retrieval_enabled": True,
            "kb_personal_enabled": True,
            "kb_slugs_filter": None,
            "kb_narrow": False,
            "version": 0,
            "zitadel_user_id": None,
            # Portal unreachable AND no cached settings to fall back on (the
            # stale-cache branch above already handles the common case). We
            # cannot know the user's mode, so the hook refuses honestly rather
            # than silently defaulting to a general answer.
            "settings_unavailable": True,
            # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-4: fail-open to 'shadow', never 'off'.
            # Silent telemetry is the wrong default during outages.
            "telemetry_level": "shadow",
        }

    version = data.get("kb_pref_version", 0)
    result = {
        "enabled": data.get("enabled", False),
        "kb_retrieval_enabled": data.get("kb_retrieval_enabled", True),
        "kb_personal_enabled": data.get("kb_personal_enabled", True),
        "kb_slugs_filter": data.get("kb_slugs_filter"),
        "kb_narrow": data.get("kb_narrow", False),
        "version": version,
        # SPEC-SEC-IDENTITY-ASSERT-001 follow-up: portal-api maps the LibreChat
        # ObjectId we pass in the URL to the portal_users row and exposes the
        # canonical Zitadel sub here. retrieval-api's identity-verify path,
        # personal-KB qdrant filter, and the verify-cache key all match on
        # zitadel_user_id — using the LibreChat ObjectId would 403 every call.
        "zitadel_user_id": data.get("zitadel_user_id"),
        # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-2: per-tenant telemetry mode.
        # Older portal-api builds without the field land in the default
        # 'shadow' (REQ-4 fail-open) so a mid-deploy state is privacy-safe.
        "telemetry_level": data.get("telemetry_level", "shadow"),
    }

    # Store version pointer (30s) and feature data (300s) separately
    await cache.async_set_cache(version_key, str(version), ttl=30)
    await cache.async_set_cache(
        f"kb_feature:{org_id}:{user_id}:{version}", result, ttl=300
    )
    await cache.async_set_cache(latest_feature_key, result, ttl=86400)
    return result


# ---------------------------------------------------------------------------
# SPEC-CHAT-TEMPLATES-001: prompt-template fetch.
#
# Fetched from portal-api `/internal/templates/effective` (server-side
# resolution: org → user → active_template_ids → filtered + ordered).
# Cached 30s per (org_id, user_id) via LiteLLM's shared cache, same
# pattern as get_kb_feature.
#
# Fail-open: any timeout / 5xx / 401 / bad secret → empty list + warning.
# Chat MUST never break because the templates fetch failed.
#
# @MX:WARN: This helper is fail-open by design. A silent `templates_degraded`
# log line is the ONLY signal when portal-api can't be reached. Observability
# must alert on a sustained non-zero rate of these warnings.
# @MX:REASON: templates are a convenience feature; blocking a chat call to
# preserve styling would be a worse user experience than losing the styling.
# ---------------------------------------------------------------------------


async def get_templates(org_id: str, user_id: str, cache) -> list[dict]:
    """Return active prompt-template instructions for (org, user).

    Shape: ``[{"source": "template", "name": str, "text": str}, ...]``
    Empty list when the user has no active templates, the portal-api is
    unreachable, or PORTAL_INTERNAL_SECRET is unset.

    Cached 30 s per (org_id, user_id) — same TTL as KB feature flag so
    toggling active_template_ids takes effect in at most 30 s when Redis
    invalidation misses.
    """
    if not PORTAL_INTERNAL_SECRET:
        # Fail-closed on missing secret: without auth we can't call portal-api.
        return []

    cache_key = f"templates:{org_id}:{user_id}"
    cached = await cache.async_get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=TEMPLATES_TIMEOUT) as client:
            resp = await client.get(
                PORTAL_TEMPLATES_URL,
                params={"zitadel_org_id": org_id, "librechat_user_id": user_id},
                headers={"Authorization": f"Bearer {PORTAL_INTERNAL_SECRET}"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as exc:
        # 401 → config error (bad or missing internal secret); log distinctly.
        if exc.response is not None and exc.response.status_code == 401:
            logger.error(
                "KlaiKnowledgeHook: templates_config_error org=%s user=%s (bad internal secret)",
                org_id,
                user_id,
            )
        else:
            logger.warning(
                "KlaiKnowledgeHook: templates_degraded org=%s user=%s reason=%s",
                org_id,
                user_id,
                exc,
            )
        instructions: list[dict] = []
    except Exception as exc:
        logger.warning(
            "KlaiKnowledgeHook: templates_degraded org=%s user=%s reason=%s",
            org_id,
            user_id,
            exc,
        )
        instructions = []
    else:
        instructions = payload.get("instructions") or []

    # Cache even the empty result: a user with no active templates shouldn't
    # retry the portal round-trip on every message.
    await cache.async_set_cache(cache_key, instructions, ttl=30)
    return instructions
