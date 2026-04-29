"""Redis-backed nonce store for Zitadel webhook replay protection.

SPEC-SEC-MAILER-INJECTION-001 REQ-6. Records seen `(timestamp, v1)` pairs in
Redis with a 5-min TTL matching the signature replay window. A second webhook
with the same pair within the window is rejected as a replay.

Fail-closed posture (REQ-6.3): Redis unreachable → `RedisUnavailableError`.
Callers map this to HTTP 503 `{"detail": "Service unavailable"}`. The nonce
check is a security control, not an availability control — a failed nonce
check is an immediate security signal.

REQ-6.4: the nonce check runs AFTER HMAC verification. Forged signatures
never reach this module, so the cache is not polluted by attacker noise.

Test hook:
    import app.nonce as nonce
    nonce.set_redis_client(fakeredis_instance)

Production:
    app.nonce initialises a singleton client lazily from settings.redis_url.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings
from app.signature import REPLAY_WINDOW_SECONDS

logger = logging.getLogger(__name__)


class NonceReplayError(Exception):
    """Raised when `(t, v1)` has been seen within the replay window."""


class RedisUnavailableError(Exception):
    """Raised when the Redis call failed (connection, timeout, etc.).

    Also raised when ``REDIS_URL`` is structurally invalid — see
    ``get_redis`` for the translation rationale.
    """


# Module-level client — settable from tests via set_redis_client().
_redis_client: Any = None


def set_redis_client(client: Any) -> None:
    """Test-only hook to inject a fakeredis / broken-redis instance."""
    global _redis_client
    _redis_client = client


def reset_redis_client() -> None:
    """Release the singleton, forcing a fresh connection on next get_redis()."""
    global _redis_client
    _redis_client = None


def get_redis() -> Any:
    """Return the module-level redis asyncio client, creating it lazily.

    Uses ``parse_redis_url`` instead of ``redis_asyncio.from_url`` because
    the latter delegates to ``urllib.parse.urlparse``, which raises
    ``ValueError("Port could not be cast")`` on URLs whose password
    contains reserved characters (``:``, ``/``, ``+``, ``@``) that the
    operator forgot to percent-encode in SOPS. By peeling the userinfo
    off structurally and passing fields as kwargs to
    ``redis_asyncio.Redis``, the password is treated as opaque bytes.
    See ``app/redis_url.py`` for the full rationale.

    A structurally-broken URL (no scheme, no host) raises ``RedisURLError``,
    which this function translates to ``RedisUnavailableError`` so the
    /notify handler returns the same 503 it would for a runtime Redis
    outage. The translation log line ``mailer_redis_url_invalid`` is the
    operator-visible signal.
    """
    global _redis_client
    if _redis_client is None:
        # Lazy import so test overrides can install a stub before first use
        # without pulling redis-py into process memory unnecessarily.
        import redis.asyncio as redis_asyncio

        from app.redis_url import RedisURLError, parse_redis_url

        try:
            parsed = parse_redis_url(settings.redis_url)
        except RedisURLError as exc:
            logger.error("mailer_redis_url_invalid: %s", exc)
            raise RedisUnavailableError(f"REDIS_URL is malformed: {exc}") from exc
        kwargs: dict[str, Any] = {
            "host": parsed.host,
            "port": parsed.port,
            "username": parsed.username,
            "password": parsed.password,
            "db": parsed.db,
            "decode_responses": False,
            "socket_timeout": 2.0,
            "socket_connect_timeout": 2.0,
        }
        if parsed.use_ssl:
            kwargs["ssl"] = True
        _redis_client = redis_asyncio.Redis(**kwargs)
    return _redis_client


def _nonce_key(parts: dict[str, str]) -> str:
    """`mailer:nonce:<timestamp>:<v1>` — REQ-6.1 / REQ-6.2 exact key format."""
    return f"mailer:nonce:{parts['t']}:{parts['v1']}"


async def check_and_record_nonce(parts: dict[str, str]) -> None:
    """Record the nonce in Redis via SET NX EX 300.

    - New key → returns None (accepted).
    - Existing key (replay) → raises `NonceReplayError`.
    - Redis outage → raises `RedisUnavailableError`.

    Arguments:
      parts: the parsed signature dict from `verify_zitadel_signature`,
             at minimum `{"t": ..., "v1": ...}`.
    """
    client = get_redis()
    key = _nonce_key(parts)
    try:
        # set(..., nx=True, ex=TTL) returns True when the key was created,
        # None/False when it already existed.
        recorded = await client.set(key, b"1", nx=True, ex=REPLAY_WINDOW_SECONDS)
    except Exception as exc:
        raise RedisUnavailableError(str(exc)) from exc

    if not recorded:
        raise NonceReplayError(key)
