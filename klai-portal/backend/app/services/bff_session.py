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

import hashlib
import json
import secrets
import time
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


class SessionService:
    """
    CRUD + helpers for the BFF session store.

    Intentionally stateless: obtains a Redis connection per call from the
    shared pool. Singleton instance `session_service` at the bottom of the
    module matches the pattern used by `zitadel` elsewhere in portal-api.
    """

    def __init__(self) -> None:
        self._fernet: Fernet | None = None

    # ------------------------------------------------------------------ crypto

    def _get_fernet(self) -> Fernet:
        """Lazy-init Fernet so we do not crash at import time if the key is unset."""
        if self._fernet is not None:
            return self._fernet
        key = settings.bff_session_key or settings.sso_cookie_key
        if not key:
            raise RuntimeError(
                "BFF_SESSION_KEY (or SSO_COOKIE_KEY fallback) is not configured"
            )
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

    # ------------------------------------------------------------------- pool

    async def _optional_pool(self) -> redis.Redis | None:
        return await get_redis_pool()

    async def _require_pool(self) -> redis.Redis:
        pool = await get_redis_pool()
        if pool is None:
            raise RuntimeError("Redis pool is unavailable; BFF session store disabled")
        return pool


session_service = SessionService()
