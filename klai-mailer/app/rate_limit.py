"""Redis-backed per-recipient-email sliding-window rate limiter.

SPEC-SEC-MAILER-INJECTION-001 REQ-4. Caps per-recipient send volume at
`settings.mailer_rate_limit_per_recipient` sends within the trailing
`settings.mailer_rate_limit_window_seconds` window.

Keying (REQ-4.2): `mailer:rl:<sha256(lowercase-recipient-email)>` — the
recipient email itself is NEVER stored in Redis. A leaked Redis access log
does not leak the recipient list.

Fail-mode (REQ-4.3): Redis unreachable → fail OPEN (allow the send) +
emit `mailer_rate_limit_redis_unavailable` log event. This is the opposite
of the nonce path (REQ-6.3 fails closed). Rationale: a failed nonce check
is an immediate security signal; a failed rate-limit check is degraded
monitoring. Hard-blocking all sends on Redis outage would break incident
response email.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

import structlog

from app.config import settings

logger = structlog.get_logger()

# Module-level client, settable from tests like app.nonce.
_redis_client: Any = None


def set_redis_client(client: Any) -> None:
    """Test-only hook to inject a fakeredis / broken-redis instance."""
    global _redis_client
    _redis_client = client


def reset_redis_client() -> None:
    """Release the singleton."""
    global _redis_client
    _redis_client = None


def get_redis() -> Any:
    """Return the module-level redis asyncio client, creating lazily."""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as redis_asyncio
        _redis_client = redis_asyncio.from_url(
            settings.redis_url,
            decode_responses=False,
            socket_timeout=2.0,
            socket_connect_timeout=2.0,
        )
    return _redis_client


@dataclass(slots=True)
class RateLimitDecision:
    """Outcome of a rate-limit check. Only `allowed=False` rejects the send."""

    allowed: bool
    retry_after_seconds: int = 0
    recipient_hash: str = ""


def _recipient_hash(email: str) -> str:
    """REQ-4.2: SHA-256 of the lowercase email address."""
    normalised = email.strip().lower().encode("utf-8")
    return hashlib.sha256(normalised).hexdigest()


def _redis_key(recipient_hash: str) -> str:
    return f"mailer:rl:{recipient_hash}"


async def check_and_record(
    recipient_email: str,
    *,
    ceiling: int | None = None,
    window_seconds: int | None = None,
    now: int | None = None,
) -> RateLimitDecision:
    """Return a RateLimitDecision AFTER recording this attempt.

    Sliding window implemented via a Redis sorted set whose scores are the
    submission timestamps. We:
    1. Purge entries older than `now - window_seconds`.
    2. Count remaining entries.
    3. If count >= ceiling, reject with a Retry-After computed from the
       oldest remaining entry.
    4. Otherwise, add this attempt and set the TTL.

    REQ-4.3: on any Redis error, return `allowed=True` and log warn. The
    caller MUST NOT treat this as an auth bypass — rate limiting is
    additional defense, not primary.
    """
    cap = ceiling if ceiling is not None else settings.mailer_rate_limit_per_recipient
    window = (
        window_seconds if window_seconds is not None else settings.mailer_rate_limit_window_seconds
    )
    now_ts = now if now is not None else int(time.time())

    recipient_hash = _recipient_hash(recipient_email)
    key = _redis_key(recipient_hash)

    # Unique member so each call occupies its own sorted-set slot even when
    # two calls share a timestamp second. Score stays `now_ts` so
    # zremrangebyscore can purge by time.
    member = f"{now_ts}:{secrets.token_hex(8)}"

    try:
        client = get_redis()
        async with client.pipeline(transaction=True) as pipe:
            # Purge + count + add + expire in one round trip
            pipe.zremrangebyscore(key, 0, now_ts - window)
            pipe.zcard(key)
            pipe.zadd(key, {member: now_ts})
            pipe.expire(key, window)
            _, count_before_add, _, _ = await pipe.execute()

        # count_before_add is the number of entries after the purge but
        # BEFORE our zadd — i.e. the number of prior sends in the window.
        if count_before_add >= cap:
            # Compute retry-after from oldest entry's timestamp.
            oldest = await client.zrange(key, 0, 0, withscores=True)
            if oldest:
                _, oldest_score = oldest[0]
                retry_after = max(1, int(oldest_score) + window - now_ts)
            else:
                retry_after = window
            # Our attempt was added; remove it so a denied request does
            # not count toward the budget.
            await client.zrem(key, member)
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=retry_after,
                recipient_hash=recipient_hash,
            )
        return RateLimitDecision(allowed=True, recipient_hash=recipient_hash)

    except Exception as exc:  # REQ-4.3: fail OPEN on any Redis fault
        logger.warning("mailer_rate_limit_redis_unavailable", error=str(exc))
        return RateLimitDecision(allowed=True, recipient_hash=recipient_hash)
