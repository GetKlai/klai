"""Redis-backed cache for ``/internal/identity/verify`` decisions.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-1.5 / REQ-1.6:

- Cache successful verifications for 60 seconds, keyed on
  ``(caller_service, claimed_user_id, claimed_org_id, evidence)``.
- Cache hits skip both DB and JWT signature re-check — the cached evidence
  is the authoritative answer for the TTL window.
- Denials are NEVER cached (matches consumer-side behaviour and prevents
  poisoning the cache with transient deny states).
- IF Redis is unreachable, fail CLOSED — raise :class:`CacheUnavailable`,
  which the endpoint maps to HTTP 503 ``cache_unavailable``. This is the
  deliberate inverse of SPEC-SEC-005's rate-limiter (which fails open):
  an auth-class control must not silently downgrade to "always allow".

Cache key includes ``caller_service`` so different consumers do not share
cache entries — privacy boundary enforced at the key level.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

from app.services.identity_verifier import VerifyDecision

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Distinct key namespace from other Klai Redis consumers (rate-limit, sessions).
_KEY_PREFIX = "identity_verify:"

# REQ-1.5: 60-second TTL. Cap at 60 — caller-side worst case for revocation
# propagation. Lowering the TTL is allowed (faster revocation), raising is not.
_TTL_SECONDS = 60


class CacheUnavailable(Exception):
    """Redis call failed and the endpoint MUST fail closed (HTTP 503)."""


def _build_key(*, caller_service: str, claimed_user_id: str, claimed_org_id: str) -> str:
    """Build a Redis key from the verifier inputs.

    Note: ``evidence`` is part of the cached *value*, not the key. The key
    spans both JWT and membership outcomes for the same tuple — whichever
    decision lands first wins for the TTL window. This is deliberate: a
    successful JWT verification does not weaken when followed by a
    membership-only call (the user's permissions are unchanged).
    """

    return f"{_KEY_PREFIX}{caller_service}:{claimed_user_id}:{claimed_org_id}"


async def get_cached_decision(
    *,
    redis: Redis,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
) -> VerifyDecision | None:
    """Return a cached verified decision, or ``None`` on miss.

    Raises
    ------
    CacheUnavailable
        On any Redis error. The endpoint MUST translate this to HTTP 503.
    """

    key = _build_key(
        caller_service=caller_service,
        claimed_user_id=claimed_user_id,
        claimed_org_id=claimed_org_id,
    )
    try:
        raw: bytes | str | None = await redis.get(key)
    except RedisError as exc:
        logger.warning("identity_verify_cache_get_failed", extra={"error": str(exc)})
        raise CacheUnavailable("redis_get_failed") from exc

    if raw is None:
        return None
    payload_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        # Corrupt entry — treat as miss. Don't fail closed: a stale corrupt
        # entry should not lock callers out; the next call will refresh it.
        logger.warning("identity_verify_cache_corrupt", extra={"key": key})
        return None
    if not isinstance(payload, dict):
        return None
    evidence = payload.get("evidence")
    user_id = payload.get("user_id")
    org_id = payload.get("org_id")
    if evidence not in ("jwt", "membership"):
        return None
    if not isinstance(user_id, str) or not isinstance(org_id, str):
        return None
    return VerifyDecision.allow(user_id=user_id, org_id=org_id, evidence=evidence)


async def cache_verified_decision(
    *,
    redis: Redis,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
    decision: VerifyDecision,
) -> None:
    """Cache a verified decision. No-op for denials.

    Raises
    ------
    CacheUnavailable
        On any Redis error. The endpoint MUST translate this to HTTP 503
        rather than returning the verified decision without caching it —
        otherwise a Redis flap would silently disable the cache and amplify
        DB load for every subsequent call.
    """

    if not decision.verified or decision.evidence is None or decision.user_id is None or decision.org_id is None:
        return
    key = _build_key(
        caller_service=caller_service,
        claimed_user_id=claimed_user_id,
        claimed_org_id=claimed_org_id,
    )
    payload = json.dumps(
        {
            "user_id": decision.user_id,
            "org_id": decision.org_id,
            "evidence": decision.evidence,
        }
    )
    try:
        await redis.set(key, payload, ex=_TTL_SECONDS)
    except RedisError as exc:
        logger.warning("identity_verify_cache_set_failed", extra={"error": str(exc)})
        raise CacheUnavailable("redis_set_failed") from exc
