"""Per-process TTL cache for verified identity tuples.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-7.2: each consumer caches successful verify
results for 60 seconds, keyed on the full tuple
``(caller_service, claimed_user_id, claimed_org_id, hash(bearer_jwt or "none"))``.

Privacy boundary: the cache is per-process (NOT redis-backed at the consumer
side). A shared cache across consumers would let one service infer that
another service just looked up the same user — a cross-service privacy smell
that research.md §2.4 explicitly rules out. Portal-api keeps its own
redis-backed cache on the verifier side; the consumer cache is independent.

Negative caching: this cache stores only ``verified=True`` results. Denials
are never cached (mirrors portal REQ-1.5). Re-querying after a deny gives the
caller a chance to recover (e.g. user just got their membership granted).
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from klai_identity_assert.models import VerifyResult


@dataclass(frozen=True, slots=True)
class _CacheKey:
    caller_service: str
    claimed_user_id: str
    claimed_org_id: str
    jwt_fingerprint: str


@dataclass(frozen=True, slots=True)
class _TenantCacheKey:
    """Cache key for tenant-only entries — no user_id, no JWT fingerprint.

    Kept as a separate dataclass (not a mode flag on _CacheKey) so there is
    zero possibility of a user-bound key and a tenant-only key comparing equal
    even when both carry the same (caller_service, claimed_org_id).
    """

    caller_service: str
    claimed_org_id: str


def _fingerprint_jwt(bearer_jwt: str | None) -> str:
    """Return a fingerprint that distinguishes JWT-bearing from JWT-less calls.

    Why hash and not store the JWT itself: the cache lives in process memory
    next to the rest of the verified identity — leaking a JWT into a cache
    key would put it inside heap dumps and tracebacks. The hash is sufficient
    to distinguish "same JWT" from "different JWT" within the 60s window.
    """

    if bearer_jwt is None:
        return "none"
    digest = hashlib.sha256(bearer_jwt.encode("utf-8")).hexdigest()
    return digest[:16]  # 64 bits is plenty to avoid collisions in 60s


class IdentityCache:
    """Bounded LRU cache with monotonic-clock TTL.

    Thread-safe: holds a lock around mutations. Async callers do not need
    awaitable locking because operations are O(1) and the hold is microseconds.
    """

    def __init__(self, *, ttl_seconds: float = 60.0, max_entries: int = 1024) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._store: OrderedDict[_CacheKey, tuple[float, VerifyResult]] = OrderedDict()
        # Separate store for tenant-only entries — kept independent so a user-bound
        # key and a tenant-only key can never collide, even sharing the same org.
        self._tenant_store: OrderedDict[_TenantCacheKey, tuple[float, VerifyResult]] = OrderedDict()
        self._lock = threading.Lock()

    def _key(
        self,
        caller_service: str,
        claimed_user_id: str,
        claimed_org_id: str,
        bearer_jwt: str | None,
    ) -> _CacheKey:
        return _CacheKey(
            caller_service=caller_service,
            claimed_user_id=claimed_user_id,
            claimed_org_id=claimed_org_id,
            jwt_fingerprint=_fingerprint_jwt(bearer_jwt),
        )

    def get(
        self,
        *,
        caller_service: str,
        claimed_user_id: str,
        claimed_org_id: str,
        bearer_jwt: str | None,
        now: float | None = None,
    ) -> VerifyResult | None:
        """Return a cached verified result, or ``None`` on miss / expiry.

        ``now`` is exposed for deterministic testing; production callers omit
        it and the cache uses :func:`time.monotonic`.
        """

        key = self._key(caller_service, claimed_user_id, claimed_org_id, bearer_jwt)
        current = time.monotonic() if now is None else now
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if current >= expires_at:
                # Lazy eviction on miss — the entry is gone for any subsequent caller.
                del self._store[key]
                return None
            # LRU touch: move to end so it survives the next sweep.
            self._store.move_to_end(key)
            # Mark cached=True without losing the original verified state.
            return VerifyResult(
                verified=result.verified,
                user_id=result.user_id,
                org_id=result.org_id,
                org_slug=result.org_slug,
                reason=result.reason,
                evidence=result.evidence,
                cached=True,
            )

    def put(
        self,
        *,
        caller_service: str,
        claimed_user_id: str,
        claimed_org_id: str,
        bearer_jwt: str | None,
        result: VerifyResult,
        now: float | None = None,
    ) -> None:
        """Cache a verified result. No-op for non-verified results (REQ-1.5)."""

        if not result.verified:
            return
        key = self._key(caller_service, claimed_user_id, claimed_org_id, bearer_jwt)
        current = time.monotonic() if now is None else now
        with self._lock:
            self._store[key] = (current + self._ttl_seconds, result)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)

    def get_tenant(
        self,
        *,
        caller_service: str,
        claimed_org_id: str,
        now: float | None = None,
    ) -> VerifyResult | None:
        """Return a cached tenant-only verified result, or ``None`` on miss / expiry."""
        key = _TenantCacheKey(caller_service=caller_service, claimed_org_id=claimed_org_id)
        current = time.monotonic() if now is None else now
        with self._lock:
            entry = self._tenant_store.get(key)
            if entry is None:
                return None
            expires_at, result = entry
            if current >= expires_at:
                del self._tenant_store[key]
                return None
            self._tenant_store.move_to_end(key)
            return VerifyResult(
                verified=result.verified,
                user_id=result.user_id,
                org_id=result.org_id,
                org_slug=result.org_slug,
                reason=result.reason,
                evidence=result.evidence,
                cached=True,
            )

    def put_tenant(
        self,
        *,
        caller_service: str,
        claimed_org_id: str,
        result: VerifyResult,
        now: float | None = None,
    ) -> None:
        """Cache a tenant-only verified result. No-op for non-verified results."""
        if not result.verified:
            return
        key = _TenantCacheKey(caller_service=caller_service, claimed_org_id=claimed_org_id)
        current = time.monotonic() if now is None else now
        with self._lock:
            self._tenant_store[key] = (current + self._ttl_seconds, result)
            self._tenant_store.move_to_end(key)
            while len(self._tenant_store) > self._max_entries:
                self._tenant_store.popitem(last=False)

    def clear(self) -> None:
        """Drop every cached entry. Test-only."""
        with self._lock:
            self._store.clear()
            self._tenant_store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
