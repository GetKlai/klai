"""Unit tests for BFF SessionService (SPEC-AUTH-008)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.session import SESSION_KEY_PREFIX
from app.services.bff_session import (
    SessionDecryptError,
    SessionRecord,
    SessionService,
)


@pytest.fixture(autouse=True)
def _configure_fernet(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "bff_session_key", key)
    monkeypatch.setattr(settings, "sso_cookie_key", "")
    monkeypatch.setattr(settings, "bff_session_ttl_seconds", 86400)


@pytest.fixture
def fake_redis() -> AsyncMock:
    store: dict[str, Any] = {}

    async def fake_get(key: str) -> bytes | None:
        return store.get(key)

    async def fake_set(key: str, value: bytes, ex: int | None = None) -> None:
        store[key] = value

    async def fake_delete(*keys: str) -> int:
        count = 0
        for key in keys:
            if key in store:
                del store[key]
                count += 1
        return count

    pool = AsyncMock()
    pool.get.side_effect = fake_get
    pool.set.side_effect = fake_set
    pool.delete.side_effect = fake_delete
    pool._store = store  # expose for test introspection  # type: ignore[attr-defined]
    return pool


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch, fake_redis: AsyncMock) -> SessionService:
    svc = SessionService()

    async def get_pool() -> AsyncMock:
        return fake_redis

    monkeypatch.setattr("app.services.bff_session.get_redis_pool", get_pool)
    return svc


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_new_sid_has_enough_entropy(self) -> None:
        sid = SessionService.new_sid()
        # 32 bytes → 43-char urlsafe base64 without padding
        assert len(sid) == 43
        assert sid.replace("-", "").replace("_", "").isalnum()

    def test_new_sid_is_unique(self) -> None:
        ids = {SessionService.new_sid() for _ in range(100)}
        assert len(ids) == 100

    def test_new_csrf_token_is_unique(self) -> None:
        tokens = {SessionService.new_csrf_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_hash_metadata_deterministic(self) -> None:
        assert SessionService.hash_metadata("Mozilla/5.0") == SessionService.hash_metadata("Mozilla/5.0")

    def test_hash_metadata_differentiates_values(self) -> None:
        assert SessionService.hash_metadata("A") != SessionService.hash_metadata("B")

    def test_hash_metadata_empty_returns_empty(self) -> None:
        assert SessionService.hash_metadata(None) == ""
        assert SessionService.hash_metadata("") == ""

    def test_session_key_prefix(self) -> None:
        assert SessionService.session_key("abc").startswith(SESSION_KEY_PREFIX)


# ---------------------------------------------------------------------------
# SessionRecord round-trip
# ---------------------------------------------------------------------------


class TestSessionRecord:
    def test_round_trip_through_json(self) -> None:
        original = SessionRecord(
            sid="s1",
            zitadel_user_id="u1",
            org_id=42,
            access_token="at",
            refresh_token="rt",
            access_token_expires_at=1234,
            id_token="idt",
            csrf_token="csrf",
            created_at=100,
            last_seen_at=200,
            user_agent_hash="uah",
            ip_hash="iph",
        )
        restored = SessionRecord.from_json(original.to_json())
        assert restored == original


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


class TestEncryption:
    def test_encrypt_decrypt_round_trip(self, service: SessionService) -> None:
        blob = service._encrypt("hello world")
        assert blob != b"hello world"
        assert service._decrypt(blob) == "hello world"

    def test_decrypt_rejects_tampered_blob(self, service: SessionService) -> None:
        blob = service._encrypt("hello")
        tampered = blob[:-1] + bytes([blob[-1] ^ 0x01])
        with pytest.raises(SessionDecryptError):
            service._decrypt(tampered)

    def test_missing_key_raises_on_first_use(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "bff_session_key", "")
        monkeypatch.setattr(settings, "sso_cookie_key", "")
        svc = SessionService()
        with pytest.raises(RuntimeError, match="BFF_SESSION_KEY"):
            svc._encrypt("anything")

    def test_sso_cookie_key_is_used_as_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "bff_session_key", "")
        monkeypatch.setattr(settings, "sso_cookie_key", Fernet.generate_key().decode())
        svc = SessionService()
        blob = svc._encrypt("fallback")
        assert svc._decrypt(blob) == "fallback"


# ---------------------------------------------------------------------------
# CRUD against fake Redis
# ---------------------------------------------------------------------------


class TestCreateLoadRevoke:
    @pytest.mark.asyncio
    async def test_create_stores_encrypted_record(self, service: SessionService, fake_redis: AsyncMock) -> None:
        record = await service.create(
            zitadel_user_id="user-1",
            org_id=7,
            access_token="at-1",
            refresh_token="rt-1",
            access_token_expires_at=9999,
            id_token="idt-1",
            user_agent="Mozilla",
            remote_ip="1.2.3.4",
        )
        key = service.session_key(record.sid)
        stored = fake_redis._store[key]
        # Ciphertext must NOT contain the plaintext tokens
        assert b"at-1" not in stored
        assert b"rt-1" not in stored
        # But it decrypts back to a record with the right fields
        decrypted = service._decrypt(stored)
        restored = SessionRecord.from_json(decrypted)
        assert restored.zitadel_user_id == "user-1"
        assert restored.org_id == 7
        assert restored.access_token == "at-1"
        assert restored.refresh_token == "rt-1"
        assert restored.csrf_token  # populated

    @pytest.mark.asyncio
    async def test_load_returns_none_when_missing(self, service: SessionService) -> None:
        assert await service.load("does-not-exist") is None

    @pytest.mark.asyncio
    async def test_load_round_trips_created_record(self, service: SessionService) -> None:
        created = await service.create(
            zitadel_user_id="u",
            org_id=None,
            access_token="a",
            refresh_token="r",
            access_token_expires_at=1,
            id_token="i",
            user_agent=None,
            remote_ip=None,
        )
        loaded = await service.load(created.sid)
        assert loaded is not None
        assert loaded.sid == created.sid
        assert loaded.access_token == "a"
        assert loaded.csrf_token == created.csrf_token

    @pytest.mark.asyncio
    async def test_load_returns_none_and_purges_undecryptable_blob(
        self,
        service: SessionService,
        fake_redis: AsyncMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Write a blob encrypted with a DIFFERENT key to simulate key rotation.
        other = SessionService()
        monkeypatch.setattr(settings, "bff_session_key", Fernet.generate_key().decode())
        monkeypatch.setattr(settings, "sso_cookie_key", "")
        junk = other._encrypt("{}")
        fake_redis._store[service.session_key("stale")] = junk
        # The active service uses a different key; it must purge and return None.
        monkeypatch.setattr(settings, "bff_session_key", Fernet.generate_key().decode())
        loaded = await service.load("stale")
        assert loaded is None
        assert service.session_key("stale") not in fake_redis._store

    @pytest.mark.asyncio
    async def test_update_rewrites_payload(self, service: SessionService, fake_redis: AsyncMock) -> None:
        record = await service.create(
            zitadel_user_id="u",
            org_id=None,
            access_token="old-at",
            refresh_token="old-rt",
            access_token_expires_at=100,
            id_token="i",
            user_agent=None,
            remote_ip=None,
        )
        record.access_token = "new-at"
        record.access_token_expires_at = 200
        await service.update(record)
        loaded = await service.load(record.sid)
        assert loaded is not None
        assert loaded.access_token == "new-at"
        assert loaded.access_token_expires_at == 200
        assert loaded.last_seen_at >= record.created_at

    @pytest.mark.asyncio
    async def test_revoke_removes_key(self, service: SessionService, fake_redis: AsyncMock) -> None:
        record = await service.create(
            zitadel_user_id="u",
            org_id=None,
            access_token="a",
            refresh_token="r",
            access_token_expires_at=1,
            id_token="i",
            user_agent=None,
            remote_ip=None,
        )
        assert await service.revoke(record.sid) is True
        assert await service.load(record.sid) is None
        assert await service.revoke(record.sid) is False  # idempotent


# ---------------------------------------------------------------------------
# Pool-unavailable behaviour
# ---------------------------------------------------------------------------


class TestPoolUnavailable:
    @pytest.mark.asyncio
    async def test_load_returns_none_when_pool_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = SessionService()

        async def no_pool() -> None:
            return None

        monkeypatch.setattr("app.services.bff_session.get_redis_pool", no_pool)
        assert await svc.load("any") is None

    @pytest.mark.asyncio
    async def test_create_raises_when_pool_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = SessionService()

        async def no_pool() -> None:
            return None

        monkeypatch.setattr("app.services.bff_session.get_redis_pool", no_pool)
        with pytest.raises(RuntimeError, match="Redis pool is unavailable"):
            await svc.create(
                zitadel_user_id="u",
                org_id=None,
                access_token="a",
                refresh_token="r",
                access_token_expires_at=1,
                id_token="i",
                user_agent=None,
                remote_ip=None,
            )
