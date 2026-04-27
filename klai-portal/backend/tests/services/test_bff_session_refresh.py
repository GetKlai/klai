"""Unit tests for SessionService.refresh_if_needed (SPEC-AUTH-008).

Covers the singleflight coalescing path added to the session service so a
BFF session can survive longer than the 30-60 minute Zitadel access-token
TTL without user-visible 401s.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.services.bff_oidc import OidcFlowError, TokenSet
from app.services.bff_session import SessionRecord, SessionService


@pytest.fixture(autouse=True)
def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bff_session_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    monkeypatch.setattr(settings, "bff_session_ttl_seconds", 86400)
    monkeypatch.setattr(settings, "bff_access_token_skew_seconds", 60)


@pytest.fixture
def fake_redis() -> AsyncMock:
    store: dict[str, Any] = {}

    async def fake_get(key: str) -> bytes | None:
        return store.get(key)

    async def fake_set(key: str, value: bytes, ex: int | None = None) -> None:
        store[key] = value

    async def fake_delete(*keys: str) -> int:
        count = sum(1 for k in keys if k in store)
        for k in keys:
            store.pop(k, None)
        return count

    async def fake_exists(*keys: str) -> int:
        return sum(1 for k in keys if k in store)

    pool = AsyncMock()
    pool.get.side_effect = fake_get
    pool.set.side_effect = fake_set
    pool.delete.side_effect = fake_delete
    pool.exists.side_effect = fake_exists
    pool._store = store
    return pool


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock) -> SessionService:
    svc = SessionService()

    async def get_pool() -> AsyncMock:
        return fake_redis

    monkeypatch.setattr("app.services.bff_session.get_redis_pool", get_pool)
    return svc


async def _seed_session(service: SessionService, *, expires_at: int, access_token: str = "at-initial") -> SessionRecord:
    return await service.create(
        zitadel_user_id="user-1",
        org_id=1,
        access_token=access_token,
        refresh_token="rt-initial",
        access_token_expires_at=expires_at,
        id_token="idt-initial",
        user_agent=None,
        remote_ip=None,
    )


class TestRefreshSkipsFreshTokens:
    @pytest.mark.asyncio
    async def test_fresh_token_is_returned_unchanged(self, service: SessionService) -> None:
        far_future = int(time.time()) + 3600  # 1h out, well beyond the 60s skew
        record = await _seed_session(service, expires_at=far_future)

        # No Zitadel call should happen — make it observable.
        service._do_refresh = AsyncMock(side_effect=AssertionError("should not refresh"))

        result = await service.refresh_if_needed(record)
        assert result is record
        service._do_refresh.assert_not_awaited()


class TestRefreshWhenNearExpiry:
    @pytest.mark.asyncio
    async def test_stale_token_is_refreshed_and_persisted(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        near_past = int(time.time()) - 5  # already expired
        record = await _seed_session(service, expires_at=near_past, access_token="at-old")

        async def fake_refresh(_rt: str) -> TokenSet:
            return TokenSet(
                access_token="at-new",
                refresh_token="rt-new",
                id_token="idt-new",
                expires_in=3600,
            )

        monkeypatch.setattr("app.services.bff_session.refresh_access_token", fake_refresh, raising=False)
        # Patch the local import done inside _do_refresh too.
        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", fake_refresh)

        refreshed = await service.refresh_if_needed(record)
        assert refreshed is not None
        assert refreshed.access_token == "at-new"
        assert refreshed.refresh_token == "rt-new"
        assert refreshed.id_token == "idt-new"
        assert refreshed.access_token_expires_at > int(time.time()) + 3500

        # The record was written back to Redis.
        reloaded = await service.load(record.sid)
        assert reloaded is not None
        assert reloaded.access_token == "at-new"


class TestRefreshFailureRevokesSession:
    @pytest.mark.asyncio
    async def test_oidc_error_revokes_session(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock
    ) -> None:
        record = await _seed_session(service, expires_at=int(time.time()) - 5)

        async def failing_refresh(_rt: str) -> TokenSet:
            raise OidcFlowError("invalid_grant", "refresh token revoked")

        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", failing_refresh)

        result = await service.refresh_if_needed(record)
        assert result is None
        # Session must be gone.
        assert service.session_key(record.sid) not in fake_redis._store

    @pytest.mark.asyncio
    async def test_network_error_keeps_session_alive(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock
    ) -> None:
        """Transient errors must NOT revoke — a 30-second Zitadel outage should
        not sign every user out. The current request returns None (forcing a
        401 downstream) but the session record stays so the next request can
        retry the refresh when Zitadel is back."""
        record = await _seed_session(service, expires_at=int(time.time()) - 5)

        async def blowup(_rt: str) -> TokenSet:
            raise RuntimeError("DNS timeout")

        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", blowup)

        result = await service.refresh_if_needed(record)
        assert result is None
        # Session remains in Redis — next request will retry.
        assert service.session_key(record.sid) in fake_redis._store


class TestRefreshRaceGuard:
    @pytest.mark.asyncio
    async def test_refresh_aborts_when_session_revoked_concurrently(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock
    ) -> None:
        """Logout during refresh: the refresh must NOT resurrect the session.

        Simulates the race where the user clicks logout between our Zitadel
        refresh call and the Redis write-back.
        """
        record = await _seed_session(service, expires_at=int(time.time()) - 5)
        session_key = service.session_key(record.sid)

        async def fake_refresh_then_revoke(_rt: str) -> TokenSet:
            # Simulate: while we were waiting for Zitadel, the logout handler
            # deleted our session key.
            del fake_redis._store[session_key]
            return TokenSet(
                access_token="at-resurrected",
                refresh_token="rt-resurrected",
                id_token="idt-resurrected",
                expires_in=3600,
            )

        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", fake_refresh_then_revoke)

        result = await service.refresh_if_needed(record)
        assert result is None
        # Crucially — the session stays gone. No resurrection.
        assert session_key not in fake_redis._store


class TestSingleflightCleanup:
    @pytest.mark.asyncio
    async def test_lock_registry_is_empty_after_refresh(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ref-counted singleflight must remove its entry when the last waiter
        exits, preventing unbounded growth in long-running processes."""
        record = await _seed_session(service, expires_at=int(time.time()) - 5)

        async def fake_refresh(_rt: str) -> TokenSet:
            return TokenSet(
                access_token="at-new",
                refresh_token="rt-new",
                id_token="idt-new",
                expires_in=3600,
            )

        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", fake_refresh)

        await service.refresh_if_needed(record)
        assert service._refresh_singleflight._size() == 0

    @pytest.mark.asyncio
    async def test_lock_registry_cleans_up_after_failure(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await _seed_session(service, expires_at=int(time.time()) - 5)

        async def failing(_rt: str) -> TokenSet:
            raise OidcFlowError("invalid_grant")

        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", failing)

        await service.refresh_if_needed(record)
        assert service._refresh_singleflight._size() == 0


class TestRefreshCoalescesConcurrent:
    @pytest.mark.asyncio
    async def test_concurrent_refreshes_hit_zitadel_once(
        self, service: SessionService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record = await _seed_session(service, expires_at=int(time.time()) - 5)

        call_count = 0
        refresh_gate = asyncio.Event()

        async def slow_refresh(_rt: str) -> TokenSet:
            nonlocal call_count
            call_count += 1
            # Yield control so other waiters pile up on the lock before we
            # finish. Without coalescing they'd each increment call_count.
            await refresh_gate.wait()
            return TokenSet(
                access_token="at-new",
                refresh_token="rt-new",
                id_token="idt-new",
                expires_in=3600,
            )

        import app.services.bff_oidc as oidc

        monkeypatch.setattr(oidc, "refresh_access_token", slow_refresh)

        async def trigger() -> SessionRecord | None:
            # Each coroutine starts from its own freshly-loaded record.
            loaded = await service.load(record.sid)
            assert loaded is not None
            return await service.refresh_if_needed(loaded)

        # Launch 5 concurrent refreshers.
        pending = asyncio.gather(*[trigger() for _ in range(5)])
        # Give them a chance to queue up on the lock.
        await asyncio.sleep(0)
        refresh_gate.set()
        results = await pending

        # Exactly one Zitadel call — the other four got the already-refreshed record.
        assert call_count == 1
        # All callers saw the refreshed token.
        for r in results:
            assert r is not None
            assert r.access_token == "at-new"
