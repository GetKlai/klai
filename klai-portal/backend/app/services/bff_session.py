"""
BFF session store — SPEC-AUTH-008.

Server-side session records live in Redis, encrypted at rest with Fernet so a
compromised Redis snapshot does not leak access tokens or refresh tokens.

The session key:
    klai:session:<sid>

The value is Fernet-encrypted JSON:
    {
        "sid": "...",
        "zitadel_user_id": "...",
        "org_id": 42,
        "access_token": "...",
        "refresh_token": "...",
        "access_token_expires_at": 1713500000,
        "id_token": "...",
        "csrf_token": "...",
        "created_at": 1713490000,
        "last_seen_at": 1713490000,
        "user_agent_hash": "...",
        "ip_hash": "..."
    }

# @MX:ANCHOR fan_in=N SessionService is the single owner of the BFF session lifecycle.
# @MX:REASON Tokens must never leave this class unencrypted except to
#            SessionMiddleware (resolving request.state) and auth-bff handlers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.session import SESSION_KEY_PREFIX
from app.services.redis_client import get_redis_pool

if TYPE_CHECKING:
    import redis.asyncio as redis

logger = structlog.get_logger()


@dataclass(slots=True)
class SessionRecord:
    """Everything we persist for a logged-in user."""

    sid: str
    zitadel_user_id: str
    org_id: int | None
    access_token: str
    refresh_token: str
    access_token_expires_at: int  # unix seconds
    id_token: str
    csrf_token: str
    created_at: int
    last_seen_at: int
    user_agent_hash: str
    ip_hash: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> SessionRecord:
        data = json.loads(raw)
        return cls(**data)


class SessionNotFoundError(LookupError):
    """Raised when a requested session key is missing from Redis."""


class SessionDecryptError(RuntimeError):
    """Raised when a session blob cannot be decrypted (rotated key, corruption)."""


class _PermanentRefreshFailure(Exception):
    """OP rejected the refresh token — session is unrecoverable and must be revoked."""


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock
    waiters: int = 0


class _Singleflight:
    """Per-key coalescing lock registry with reference-counted cleanup.

    Callers acquire a lock for a given key via the async context manager;
    concurrent callers for the same key serialise behind that lock. Entries
    are removed from the registry as soon as the last waiter exits, so the
    dict does not grow unbounded in long-running processes.

    Scoped to a single asyncio event loop / process. For multi-worker
    coalescing, replace with a Redis SETNX-based distributed lock.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LockEntry] = {}
        self._mutex = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        async with self._mutex:
            entry = self._entries.get(key)
            if entry is None:
                entry = _LockEntry(lock=asyncio.Lock())
                self._entries[key] = entry
            entry.waiters += 1
            lock = entry.lock
        try:
            async with lock:
                yield
        finally:
            async with self._mutex:
                entry.waiters -= 1
                if entry.waiters == 0:
                    self._entries.pop(key, None)

    def _size(self) -> int:
        """Number of active lock entries — for test assertions."""
        return len(self._entries)


class SessionService:
    """
    CRUD + helpers for the BFF session store.

    Intentionally stateless: obtains a Redis connection per call from the
    shared pool. Singleton instance `session_service` at the bottom of the
    module matches the pattern used by `zitadel` elsewhere in portal-api.
    """

    def __init__(self) -> None:
        self._fernet: Fernet | None = None
        # Singleflight coalescing for token refresh. Entries self-clean when
        # the last waiter exits, so the dict does not grow with every session.
        self._refresh_singleflight = _Singleflight()

    # ------------------------------------------------------------------ crypto

    def _get_fernet(self) -> Fernet:
        """Lazy-init Fernet so we do not crash at import time if the key is unset."""
        if self._fernet is not None:
            return self._fernet
        key = settings.bff_session_key or settings.sso_cookie_key
        if not key:
            raise RuntimeError("BFF_SESSION_KEY (or SSO_COOKIE_KEY fallback) is not configured")
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        return self._fernet

    def _encrypt(self, payload: str) -> bytes:
        return self._get_fernet().encrypt(payload.encode())

    def _decrypt(self, blob: bytes) -> str:
        try:
            return self._get_fernet().decrypt(blob).decode()
        except InvalidToken as exc:
            raise SessionDecryptError("Session blob decryption failed") from exc

    # ------------------------------------------------------------ utilities

    @staticmethod
    def new_sid() -> str:
        """256 bits of CSPRNG entropy, URL-safe base64 encoded (43 chars, no padding)."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def new_csrf_token() -> str:
        """Separate 256 bits for the double-submit CSRF token."""
        return secrets.token_urlsafe(32)

    @staticmethod
    def hash_metadata(value: str | None) -> str:
        """Stable hash of a user-agent or IP for session-theft detection."""
        if not value:
            return ""
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def session_key(sid: str) -> str:
        return f"{SESSION_KEY_PREFIX}{sid}"

    # ------------------------------------------------------------------- CRUD

    async def create(
        self,
        *,
        zitadel_user_id: str,
        org_id: int | None,
        access_token: str,
        refresh_token: str,
        access_token_expires_at: int,
        id_token: str,
        user_agent: str | None,
        remote_ip: str | None,
    ) -> SessionRecord:
        """Create a new encrypted session in Redis and return the populated record."""
        now = int(time.time())
        record = SessionRecord(
            sid=self.new_sid(),
            zitadel_user_id=zitadel_user_id,
            org_id=org_id,
            access_token=access_token,
            refresh_token=refresh_token,
            access_token_expires_at=access_token_expires_at,
            id_token=id_token,
            csrf_token=self.new_csrf_token(),
            created_at=now,
            last_seen_at=now,
            user_agent_hash=self.hash_metadata(user_agent),
            ip_hash=self.hash_metadata(remote_ip),
        )
        pool = await self._require_pool()
        blob = self._encrypt(record.to_json())
        await pool.set(
            self.session_key(record.sid),
            blob,
            ex=settings.bff_session_ttl_seconds,
        )
        logger.info("bff_session_created", sid=record.sid, zitadel_user_id=zitadel_user_id)
        return record

    async def load(self, sid: str) -> SessionRecord | None:
        """Return the record for `sid`, or None if missing/expired/undecryptable."""
        pool = await self._optional_pool()
        if pool is None:
            return None
        raw = await pool.get(self.session_key(sid))
        if raw is None:
            return None
        try:
            decrypted = self._decrypt(raw if isinstance(raw, bytes) else raw.encode())
        except SessionDecryptError:
            logger.warning("bff_session_decrypt_failed", sid=sid)
            await pool.delete(self.session_key(sid))
            return None
        return SessionRecord.from_json(decrypted)

    async def update(self, record: SessionRecord) -> None:
        """Persist modifications to an existing record; refresh the TTL."""
        record.last_seen_at = int(time.time())
        pool = await self._require_pool()
        blob = self._encrypt(record.to_json())
        await pool.set(
            self.session_key(record.sid),
            blob,
            ex=settings.bff_session_ttl_seconds,
        )

    async def revoke(self, sid: str) -> bool:
        """Delete the Redis record. Returns True when a key was actually removed."""
        pool = await self._optional_pool()
        if pool is None:
            return False
        deleted = await pool.delete(self.session_key(sid))
        if deleted:
            logger.info("bff_session_revoked", sid=sid)
        return bool(deleted)

    # ----------------------------------------------------------- token refresh

    async def refresh_if_needed(self, record: SessionRecord) -> SessionRecord | None:
        """
        Return a SessionRecord whose access_token is not about to expire.

        Behaviour:

        - Token still fresh (>skew seconds of life left)  → return unchanged.
        - Refresh succeeds                                → return updated record.
        - OP rejects the refresh_token (invalid_grant,
          invalid_client, etc.)                            → revoke session, return None.
        - Transient failure (network, DNS, timeout)        → return None WITHOUT
          revoking; the session keeps existing and the next request retries.
          The current request's route will 401 because the access_token is past
          expiry — acceptable degradation during a Zitadel outage.
        - Session was concurrently revoked (e.g. logout)   → return None, do not
          resurrect the record (race guard in _do_refresh).

        Concurrent callers for the same sid coalesce via a ref-counted
        singleflight lock: only one Zitadel roundtrip per expiry event; waiters
        read the updated record from Redis.
        """
        skew = settings.bff_access_token_skew_seconds
        if record.access_token_expires_at - skew > int(time.time()):
            return record

        async with self._refresh_singleflight.acquire(record.sid):
            # Re-check inside the lock: another coroutine may have refreshed
            # while we were waiting. Load the freshest record from Redis.
            fresh = await self.load(record.sid)
            if fresh is None:
                return None
            if fresh.access_token_expires_at - skew > int(time.time()):
                return fresh

            try:
                return await self._do_refresh(fresh)
            except _PermanentRefreshFailure:
                # OP permanently rejected the refresh_token — wipe the session.
                await self.revoke(fresh.sid)
                return None

    async def _do_refresh(self, record: SessionRecord) -> SessionRecord | None:
        """Run one refresh round-trip.

        Returns:
            - The updated SessionRecord on success.
            - None on transient failure (network) OR concurrent revocation.
        Raises:
            - _PermanentRefreshFailure when the OP rejects the refresh token,
              signalling the caller to revoke the session.
        """
        # Local import avoids a circular dependency (bff_oidc → settings → …).
        from app.services.bff_oidc import OidcFlowError, refresh_access_token

        try:
            tokens = await refresh_access_token(record.refresh_token)
        except OidcFlowError as exc:
            logger.warning(
                "bff_session_refresh_rejected",
                sid=record.sid,
                error=exc.code,
                description=exc.description,
            )
            raise _PermanentRefreshFailure from exc
        except Exception as exc:
            logger.warning("bff_session_refresh_transient_error", sid=record.sid, error=str(exc))
            return None

        # Race guard: logout may have revoked the session while we were holding
        # the Zitadel call open. If the record is gone from Redis, refusing to
        # write makes the logout win — without this the update below would
        # resurrect the session with fresh tokens.
        if not await self._session_exists(record.sid):
            logger.info("bff_session_refresh_aborted_session_gone", sid=record.sid)
            return None

        record.access_token = tokens.access_token
        if tokens.refresh_token:
            # Zitadel rotates refresh tokens on every use (refresh_token_rotation).
            record.refresh_token = tokens.refresh_token
        record.access_token_expires_at = int(time.time()) + tokens.expires_in
        if tokens.id_token:
            record.id_token = tokens.id_token
        await self.update(record)
        logger.info("bff_session_refreshed", sid=record.sid)
        return record

    async def _session_exists(self, sid: str) -> bool:
        """Redis EXISTS without decrypt — used by the refresh race guard."""
        pool = await self._optional_pool()
        if pool is None:
            return False
        return bool(await pool.exists(self.session_key(sid)))

    # ------------------------------------------------------------------- pool

    async def _optional_pool(self) -> redis.Redis | None:
        return await get_redis_pool()

    async def _require_pool(self) -> redis.Redis:
        pool = await get_redis_pool()
        if pool is None:
            raise RuntimeError("Redis pool is unavailable; BFF session store disabled")
        return pool


session_service = SessionService()
