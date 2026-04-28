"""Redis-backed cache for ``/internal/identity/verify`` decisions.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-1.5 / REQ-1.6:

- Cache successful verifications for 60 seconds, keyed strictly on the
  ``(caller_service, claimed_user_id, claimed_org_id, evidence)`` tuple
  per REQ-1.5. The fourth coordinate keeps JWT-evidence and
  membership-evidence cache entries distinct so the ``evidence`` field
  in each response honestly reflects what was actually verified for that
  call (a membership-fallback request must NOT silently get a JWT-evidence
  cached value, or the audit signal would lie).
- The evidence dimension at lookup time is determined deterministically
  by ``bearer_jwt`` presence: a JWT-bearing request can only ever return
  ``evidence="jwt"`` (REQ-1.3 requires JWT validation when bearer_jwt is
  set; REQ-1.8 forbids fallthrough to membership when JWT is invalid).
  An ``bearer_jwt=None`` request can only return ``evidence="membership"``
  (REQ-1.4). So lookup keying on the same dimension is correct.
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

from app.services.identity_verifier import Evidence, VerifyDecision

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


def evidence_for_lookup(*, bearer_jwt: str | None) -> Evidence:
    """Determine the evidence dimension for a cache lookup deterministically.

    REQ-1.3 forces JWT-bearing requests onto the JWT path; REQ-1.4 forces
    JWT-less requests onto the membership path. The two paths never cross
    (REQ-1.8 specifically forbids invalid-JWT fallthrough), so the lookup
    can pick the dimension from ``bearer_jwt`` presence alone — without
    consulting the cache or DB.
    """

    return "jwt" if bearer_jwt is not None else "membership"


def _build_key(
    *,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
    evidence: Evidence,
) -> str:
    """Build a Redis key from the full verifier tuple including evidence.

    Including ``evidence`` makes JWT- and membership-evidence cache entries
    distinct — see module docstring for the integrity rationale.
    """

    return f"{_KEY_PREFIX}{caller_service}:{claimed_user_id}:{claimed_org_id}:{evidence}"


async def get_cached_decision(
    *,
    redis: Redis,
    caller_service: str,
    claimed_user_id: str,
    claimed_org_id: str,
    bearer_jwt: str | None,
) -> VerifyDecision | None:
    """Return a cached verified decision, or ``None`` on miss.

    The evidence dimension is derived from ``bearer_jwt`` presence (see
    :func:`evidence_for_lookup`). Callers do NOT pass evidence directly —
    the key contract is: same input → same lookup, deterministically.

    Raises
    ------
    CacheUnavailable
        On any Redis error. The endpoint MUST translate this to HTTP 503.
    """

    key = _build_key(
        caller_service=caller_service,
        claimed_user_id=claimed_user_id,
        claimed_org_id=claimed_org_id,
        evidence=evidence_for_lookup(bearer_jwt=bearer_jwt),
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
    org_slug = payload.get("org_slug")
    if evidence not in ("jwt", "membership"):
        return None
    if not isinstance(user_id, str) or not isinstance(org_id, str) or not isinstance(org_slug, str):
        return None
    return VerifyDecision.allow(user_id=user_id, org_id=org_id, org_slug=org_slug, evidence=evidence)


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

    if (
        not decision.verified
        or decision.evidence is None
        or decision.user_id is None
        or decision.org_id is None
        or decision.org_slug is None
    ):
        return
    key = _build_key(
        caller_service=caller_service,
        claimed_user_id=claimed_user_id,
        claimed_org_id=claimed_org_id,
        evidence=decision.evidence,
    )
    payload = json.dumps(
        {
            "user_id": decision.user_id,
            "org_id": decision.org_id,
            "org_slug": decision.org_slug,
            "evidence": decision.evidence,
        }
    )
    try:
        await redis.set(key, payload, ex=_TTL_SECONDS)
    except RedisError as exc:
        logger.warning("identity_verify_cache_set_failed", extra={"error": str(exc)})
        raise CacheUnavailable("redis_set_failed") from exc
