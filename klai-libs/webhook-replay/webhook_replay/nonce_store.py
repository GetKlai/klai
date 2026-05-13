"""Generic Redis-backed webhook replay-protection nonce store.

SPEC-SEC-AUTH-HARDENING-001 item 1. Extracted from klai-mailer/app/nonce.py
to a shared package so multiple services can adopt the same replay-protection
pattern without duplicating the implementation.

Fail-closed posture: Redis unreachable → ``RedisUnavailableError``.
Callers should map this to HTTP 503. The nonce check is a security control,
not an availability control — a failed nonce check is an immediate security
signal.

The nonce check MUST run AFTER HMAC verification. Forged signatures must
never reach this module, so the cache is not polluted by attacker noise.

Test hook::

    store = WebhookNonceStore(redis_url=..., prefix="svc:nonce:", ttl_seconds=300)
    store.set_client(fakeredis_instance)

Production::

    store = WebhookNonceStore(
        redis_url=settings.redis_url,
        prefix="mailer:nonce:",
        ttl_seconds=REPLAY_WINDOW_SECONDS,
    )
"""

from __future__ import annotations

import logging
from typing import Any

from webhook_replay.redis_url import RedisURLError, parse_redis_url

logger = logging.getLogger(__name__)


class NonceReplayError(Exception):
    """Raised when the nonce parts have been seen within the replay window."""


class RedisUnavailableError(Exception):
    """Raised when the Redis call failed (connection, timeout, etc.).

    Also raised when ``REDIS_URL`` is structurally invalid — see
    ``_client_or_create`` for the translation rationale.
    """


class WebhookNonceStore:
    """Generic Redis-backed replay-protection nonce store.

    Construct one per-service with a unique key prefix::

        mailer_store = WebhookNonceStore(
            redis_url=settings.redis_url,
            prefix="mailer:nonce:",
            ttl_seconds=300,
        )
        moneybird_store = WebhookNonceStore(
            redis_url=settings.redis_url,
            prefix="portal:moneybird-nonce:",
            ttl_seconds=300,
        )

    The key stored in Redis is ``{prefix}{":".join(parts)}``.
    For example, the Zitadel webhook uses parts ``(timestamp, v1_hash)``
    which produces ``mailer:nonce:1730890000:abc123``. Each webhook variant
    can pick its own shape — Moneybird uses ``(timestamp, sig_hash)``,
    Gitea uses ``(delivery_id,)``, etc.
    """

    def __init__(
        self,
        *,
        redis_url: str,
        prefix: str,
        ttl_seconds: int = 300,
    ) -> None:
        self._redis_url = redis_url
        self._prefix = prefix
        self._ttl_seconds = ttl_seconds
        # Module-level client — settable from tests via set_client().
        self._client: Any = None

    # ------------------------------------------------------------------
    # Test hooks
    # ------------------------------------------------------------------

    def set_client(self, client: Any) -> None:
        """Inject a fakeredis / broken-redis instance for tests."""
        self._client = client

    def reset_client(self) -> None:
        """Release the singleton, forcing a fresh connection on next use."""
        self._client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client_or_create(self) -> Any:
        """Return the redis asyncio client, creating it lazily.

        Uses ``parse_redis_url`` instead of ``redis_asyncio.from_url`` because
        the latter delegates to ``urllib.parse.urlparse``, which raises
        ``ValueError("Port could not be cast")`` on URLs whose password
        contains reserved characters (``:``, ``/``, ``+``, ``@``) that the
        operator forgot to percent-encode in SOPS. By peeling the userinfo
        off structurally and passing fields as kwargs to
        ``redis_asyncio.Redis``, the password is treated as opaque bytes.

        A structurally-broken URL raises ``RedisURLError``, which this
        method translates to ``RedisUnavailableError`` so the webhook
        handler returns the same 503 it would for a runtime Redis outage.
        """
        if self._client is None:
            # Lazy import so test overrides can install a stub before first use
            # without pulling redis-py into process memory unnecessarily.
            import redis.asyncio as redis_asyncio

            try:
                parsed = parse_redis_url(self._redis_url)
            except RedisURLError as exc:
                logger.error("webhook_replay_redis_url_invalid: %s", exc)
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
            self._client = redis_asyncio.Redis(**kwargs)
        return self._client

    def _nonce_key(self, parts: tuple[str, ...]) -> str:
        """Build the Redis key: ``{prefix}{parts[0]}:{parts[1]}:...``"""
        return self._prefix + ":".join(parts)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_and_record(self, *parts: str) -> None:
        """Record the nonce in Redis via SET NX EX.

        - New key → returns None (accepted).
        - Existing key (replay) → raises ``NonceReplayError``.
        - Redis outage → raises ``RedisUnavailableError``.

        Arguments:
            *parts: the nonce components. Each webhook shape picks its own
                    ordering, e.g. ``(timestamp, v1_hash)`` for Zitadel or
                    ``(delivery_id,)`` for Gitea.
        """
        client = self._client_or_create()
        key = self._nonce_key(parts)
        try:
            # set(..., nx=True, ex=TTL) returns True when the key was created,
            # None/False when it already existed.
            recorded = await client.set(key, b"1", nx=True, ex=self._ttl_seconds)
        except Exception as exc:
            raise RedisUnavailableError(str(exc)) from exc

        if not recorded:
            raise NonceReplayError(key)
