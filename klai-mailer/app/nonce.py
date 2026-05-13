"""Thin compat wrapper over klai-webhook-replay.

SPEC-SEC-AUTH-HARDENING-001 item 1: the canonical implementation moved to
klai-libs/webhook-replay. This module preserves the import path (app.nonce)
and the original check_and_record_nonce(parts: dict) signature so existing
call sites in app/main.py do not change.

The nonce key prefix remains "mailer:nonce:" for backward compatibility with
any existing Redis keys in production.
"""

from __future__ import annotations

from webhook_replay import NonceReplayError, RedisUnavailableError, WebhookNonceStore

from app.config import settings
from app.signature import REPLAY_WINDOW_SECONDS

_store = WebhookNonceStore(
    redis_url=settings.redis_url,
    prefix="mailer:nonce:",
    ttl_seconds=REPLAY_WINDOW_SECONDS,
)

# Test hooks — preserve the original module-level API so existing test helpers
# (conftest.py, test_notify_replay.py) continue to work without changes.


def set_redis_client(client: object) -> None:
    """Test-only hook: inject a fakeredis / broken-redis instance."""
    _store.set_client(client)


def reset_redis_client() -> None:
    """Test-only hook: release the singleton, forcing a fresh connection."""
    _store.reset_client()


def get_redis() -> object:
    """Return the underlying redis client (may be needed by legacy tests)."""
    return _store._client_or_create()


async def check_and_record_nonce(parts: dict[str, str]) -> None:
    """Original (t, v1) shape preserved for the Zitadel webhook handler.

    Raises NonceReplayError on replay, RedisUnavailableError on outage.
    """
    await _store.check_and_record(parts["t"], parts["v1"])


__all__ = [
    "NonceReplayError",
    "RedisUnavailableError",
    "check_and_record_nonce",
    "get_redis",
    "reset_redis_client",
    "set_redis_client",
]
