"""Tests for ConnectorCredentialStore — the per-org DEK abstraction.

Covers the acceptance criteria of SPEC-CRAWLER-004 REQ-01:
- AC-01.1 round-trip encryption per connector type
- AC-01.2 cross-org DEK isolation (auth-tag mismatch on wrong org)
- AC-01.4 missing / invalid ENCRYPTION_KEY fails loudly
- AC-01.5 KEK rotation round-trip

The store never imports any service-specific ORM model. All interaction with
``portal_orgs.connector_dek_enc`` goes through parameterized raw SQL via the
provided ``AsyncSession``; the tests verify that contract by mocking the
session.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag

from connector_credentials import (
    SENSITIVE_FIELDS,
    AESGCMCipher,
    ConnectorCredentialStore,
)

# Test-only placeholder values (not real credentials).
FAKE_TOKEN_A = "test-placeholder-token-aaa"
FAKE_TOKEN_B = "test-placeholder-token-bbb"
FAKE_TOKEN_C = "test-placeholder-token-ccc"


def _make_store(hex_key: str | None = None) -> ConnectorCredentialStore:
    if hex_key is None:
        hex_key = os.urandom(32).hex()
    return ConnectorCredentialStore(hex_key)


def _db_with_row(connector_dek_enc: bytes | None, org_exists: bool = True) -> AsyncMock:
    """Mock an AsyncSession whose SELECT returns (id, connector_dek_enc).

    When ``org_exists`` is False, ``.first()`` returns None instead of the row.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    row_mock = MagicMock()
    row_mock.id = 1
    row_mock.connector_dek_enc = connector_dek_enc

    async def fake_execute(_stmt: object, _params: object | None = None) -> MagicMock:
        result = MagicMock()
        result.first = MagicMock(return_value=row_mock if org_exists else None)
        result.scalar_one_or_none = MagicMock(return_value=row_mock if org_exists else None)
        return result

    db.execute = AsyncMock(side_effect=fake_execute)
    return db


# -- SENSITIVE_FIELDS completeness --------------------------------------------


class TestSensitiveFieldsMapping:
    def test_github_fields(self) -> None:
        assert set(SENSITIVE_FIELDS["github"]) == {
            "access_token",
            "installation_token",
            "app_private_key",
        }

    def test_notion_fields(self) -> None:
        assert SENSITIVE_FIELDS["notion"] == ["access_token"]

    def test_google_drive_fields(self) -> None:
        assert set(SENSITIVE_FIELDS["google_drive"]) == {
            "oauth_token",
            "refresh_token",
            "access_token",
        }

    def test_ms_docs_fields(self) -> None:
        assert set(SENSITIVE_FIELDS["ms_docs"]) == {
            "oauth_token",
            "refresh_token",
            "access_token",
        }

    def test_web_crawler_fields(self) -> None:
        assert set(SENSITIVE_FIELDS["web_crawler"]) == {"auth_headers", "cookies"}

    def test_all_connector_types_present(self) -> None:
        assert set(SENSITIVE_FIELDS.keys()) == {
            "github",
            "notion",
            "google_drive",
            "ms_docs",
            "web_crawler",
        }


# -- KEK validation (AC-01.4) -------------------------------------------------


class TestKEKValidation:
    """ENCRYPTION_KEY must be exactly 64 hex chars — reject otherwise."""

    def test_short_hex_rejected(self) -> None:
        with pytest.raises(ValueError, match="64-character"):
            ConnectorCredentialStore("abcd")

    def test_non_hex_rejected(self) -> None:
        with pytest.raises(ValueError, match="hex"):
            ConnectorCredentialStore("z" * 64)

    def test_odd_length_rejected(self) -> None:
        with pytest.raises(ValueError, match="64-character"):
            ConnectorCredentialStore("a" * 63)

    def test_valid_hex_accepted(self) -> None:
        _make_store()


# -- encrypt/decrypt round-trip (AC-01.1) -------------------------------------


class TestRoundTrip:
    @pytest.mark.asyncio()
    async def test_github_roundtrip(self) -> None:
        store = _make_store()
        db = AsyncMock()
        config = {
            "repo": "GetKlai/klai",
            "access_token": FAKE_TOKEN_A,
            "installation_token": FAKE_TOKEN_B,
            "app_private_key": FAKE_TOKEN_C,
        }
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            blob, stripped = await store.encrypt_credentials(org_id=1, connector_type="github", config=config, db=db)

            assert "access_token" not in stripped
            assert "installation_token" not in stripped
            assert "app_private_key" not in stripped
            assert stripped["repo"] == "GetKlai/klai"

            assert blob is not None
            decrypted = await store.decrypt_credentials(org_id=1, encrypted_credentials=blob, db=db)
            assert decrypted == {
                "access_token": FAKE_TOKEN_A,
                "installation_token": FAKE_TOKEN_B,
                "app_private_key": FAKE_TOKEN_C,
            }

    @pytest.mark.asyncio()
    async def test_web_crawler_roundtrip(self) -> None:
        store = _make_store()
        db = AsyncMock()
        config = {
            "url": "https://help.voys.nl",
            "cookies": [{"name": "session", "value": "s-abc"}],
            "auth_headers": {"X-Sso": "token-xyz"},
        }
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            blob, stripped = await store.encrypt_credentials(
                org_id=42, connector_type="web_crawler", config=config, db=db
            )
            assert "cookies" not in stripped
            assert "auth_headers" not in stripped
            assert stripped["url"] == "https://help.voys.nl"

            assert blob is not None
            decrypted = await store.decrypt_credentials(org_id=42, encrypted_credentials=blob, db=db)
            assert decrypted["cookies"] == [{"name": "session", "value": "s-abc"}]
            assert decrypted["auth_headers"] == {"X-Sso": "token-xyz"}

    @pytest.mark.asyncio()
    async def test_unknown_connector_type_is_passthrough(self) -> None:
        store = _make_store()
        db = AsyncMock()
        config = {"whatever": "value"}
        blob, stripped = await store.encrypt_credentials(org_id=1, connector_type="unknown_type", config=config, db=db)
        assert blob is None
        assert stripped == config

    @pytest.mark.asyncio()
    async def test_known_type_without_sensitive_values_returns_none_blob(self) -> None:
        store = _make_store()
        db = AsyncMock()
        # github type but config has no sensitive keys present
        config = {"repo": "GetKlai/klai"}
        blob, stripped = await store.encrypt_credentials(org_id=1, connector_type="github", config=config, db=db)
        assert blob is None
        assert stripped == config


# -- Cross-org DEK isolation (AC-01.2) ----------------------------------------


class TestCrossOrgIsolation:
    """A blob encrypted under org A's DEK cannot be decrypted under org B's DEK."""

    @pytest.mark.asyncio()
    async def test_org_b_cannot_decrypt_org_a_blob(self) -> None:
        store = _make_store()
        db = AsyncMock()
        dek_a = os.urandom(32)
        dek_b = os.urandom(32)

        # Encrypt under org A's DEK.
        with patch.object(store, "get_or_create_dek", return_value=dek_a):
            blob, _ = await store.encrypt_credentials(
                org_id=1,
                connector_type="github",
                config={"access_token": FAKE_TOKEN_A},
                db=db,
            )

        assert blob is not None
        # Attempt decrypt under org B's DEK — must raise InvalidTag.
        with patch.object(store, "get_or_create_dek", return_value=dek_b), pytest.raises(InvalidTag):
            await store.decrypt_credentials(org_id=2, encrypted_credentials=blob, db=db)


# -- Tampered ciphertext ------------------------------------------------------


class TestDecryptFromBlobs:
    """decrypt_credentials_from_blobs round-trips without a SQLAlchemy session."""

    @pytest.mark.asyncio()
    async def test_round_trip_via_blobs(self) -> None:
        """Encrypt via the session API, then decrypt via blobs only."""
        kek_hex = os.urandom(32).hex()
        store = ConnectorCredentialStore(kek_hex)

        raw_dek = os.urandom(32)
        # The DEK blob the caller would read from portal_orgs.connector_dek_enc
        # is the raw DEK's hex, encrypted under the KEK.
        connector_dek_enc = store._kek_cipher.encrypt(raw_dek.hex())  # type: ignore[attr-defined]

        # Pre-encrypt sensitive fields under that DEK.
        dek_cipher = AESGCMCipher(raw_dek)
        sensitive = {"cookies": [{"name": "sid", "value": "abc"}]}
        import json as _json

        encrypted_credentials = dek_cipher.encrypt(_json.dumps(sensitive))

        out = store.decrypt_credentials_from_blobs(
            encrypted_credentials=encrypted_credentials,
            connector_dek_enc=connector_dek_enc,
        )
        assert out == sensitive

    @pytest.mark.asyncio()
    async def test_wrong_kek_raises(self) -> None:
        """A store built with a different KEK cannot decrypt the DEK blob."""
        sender = ConnectorCredentialStore(os.urandom(32).hex())
        receiver = ConnectorCredentialStore(os.urandom(32).hex())

        raw_dek = os.urandom(32)
        dek_blob = sender._kek_cipher.encrypt(raw_dek.hex())  # type: ignore[attr-defined]
        dek_cipher = AESGCMCipher(raw_dek)
        import json as _json

        payload = dek_cipher.encrypt(_json.dumps({"cookies": [1]}))

        with pytest.raises(InvalidTag):
            receiver.decrypt_credentials_from_blobs(
                encrypted_credentials=payload,
                connector_dek_enc=dek_blob,
            )


class TestTamperedBlob:
    @pytest.mark.asyncio()
    async def test_tampered_blob_raises_invalid_tag(self) -> None:
        store = _make_store()
        db = AsyncMock()
        dek = os.urandom(32)
        with patch.object(store, "get_or_create_dek", return_value=dek):
            blob, _ = await store.encrypt_credentials(
                org_id=1,
                connector_type="github",
                config={"access_token": FAKE_TOKEN_A},
                db=db,
            )
            assert blob is not None
            tampered = bytearray(blob)
            tampered[-1] ^= 0xFF
            with pytest.raises(InvalidTag):
                await store.decrypt_credentials(
                    org_id=1,
                    encrypted_credentials=bytes(tampered),
                    db=db,
                )


# -- DEK lifecycle (SELECT FOR UPDATE + generate-on-miss) ---------------------


class TestDEKLifecycle:
    @pytest.mark.asyncio()
    async def test_generates_new_dek_when_row_exists_and_enc_is_null(self) -> None:
        store = _make_store()
        db = _db_with_row(connector_dek_enc=None, org_exists=True)

        dek = await store.get_or_create_dek(org_id=1, db=db)

        assert isinstance(dek, bytes)
        assert len(dek) == 32
        # The store must have issued: one SELECT + one UPDATE, then a flush.
        assert db.execute.await_count == 2
        db.flush.assert_awaited()

    @pytest.mark.asyncio()
    async def test_reuses_existing_dek(self) -> None:
        store = _make_store()
        raw_dek = os.urandom(32)
        encrypted_dek = store._kek_cipher.encrypt(raw_dek.hex())  # type: ignore[attr-defined]
        db = _db_with_row(connector_dek_enc=encrypted_dek, org_exists=True)

        dek = await store.get_or_create_dek(org_id=1, db=db)

        assert dek == raw_dek
        # Only the SELECT should fire — no UPDATE.
        assert db.execute.await_count == 1

    @pytest.mark.asyncio()
    async def test_missing_org_raises_value_error(self) -> None:
        store = _make_store()
        db = _db_with_row(connector_dek_enc=None, org_exists=False)

        with pytest.raises(ValueError, match="PortalOrg"):
            await store.get_or_create_dek(org_id=999, db=db)

    @pytest.mark.asyncio()
    async def test_uses_select_for_update(self) -> None:
        """The SELECT against portal_orgs must use FOR UPDATE to prevent races."""
        store = _make_store()
        db = _db_with_row(connector_dek_enc=None, org_exists=True)

        await store.get_or_create_dek(org_id=1, db=db)

        # First call is the SELECT. Inspect the statement text.
        first_call = db.execute.await_args_list[0]
        stmt_arg = first_call.args[0]
        stmt_text = str(stmt_arg)
        assert "portal_orgs" in stmt_text
        assert "FOR UPDATE" in stmt_text.upper()


# -- KEK rotation (AC-01.5) ---------------------------------------------------


class TestKEKRotation:
    """rotate_kek re-encrypts every org's DEK under a new KEK."""

    @pytest.mark.asyncio()
    async def test_rotate_round_trip(self) -> None:
        old_hex = os.urandom(32).hex()
        new_hex = os.urandom(32).hex()

        # Build three orgs with different DEKs encrypted under the OLD KEK.
        old_kek_cipher = AESGCMCipher(bytes.fromhex(old_hex))
        orgs_with_deks: list[tuple[int, bytes, bytes]] = []  # (id, raw_dek, enc_under_old)
        for org_id in (1, 2, 3):
            raw_dek = os.urandom(32)
            enc = old_kek_cipher.encrypt(raw_dek.hex())
            orgs_with_deks.append((org_id, raw_dek, enc))

        # Mock DB: first .execute returns the list of rows; subsequent UPDATEs return None.
        db = AsyncMock()
        db.flush = AsyncMock()
        rows = [MagicMock(id=oid, connector_dek_enc=enc) for oid, _, enc in orgs_with_deks]
        select_result = MagicMock()
        select_result.all = MagicMock(return_value=rows)
        select_result.first = MagicMock(return_value=None)
        update_result = MagicMock()
        update_result.all = MagicMock(return_value=[])

        call_queue: list[MagicMock] = [select_result, update_result, update_result, update_result]

        async def fake_execute(_stmt: object, _params: object | None = None) -> MagicMock:
            return call_queue.pop(0)

        db.execute = AsyncMock(side_effect=fake_execute)

        # Create the store under the OLD KEK so it can verify callers pass old_hex correctly.
        store = ConnectorCredentialStore(old_hex)
        count = await store.rotate_kek(old_kek_hex=old_hex, new_kek_hex=new_hex, db=db)

        assert count == 3
        # Capture the UPDATE parameters and confirm they decode under the NEW KEK.
        new_kek_cipher = AESGCMCipher(bytes.fromhex(new_hex))
        update_calls = db.execute.await_args_list[1:]  # skip the SELECT
        assert len(update_calls) == 3
        rotated_blobs: set[bytes] = set()
        for call in update_calls:
            params = call.args[1]
            rotated_blobs.add(params["enc"])

        # Every rotated blob must decrypt under NEW KEK and match one original DEK.
        original_deks = {raw_dek for _, raw_dek, _ in orgs_with_deks}
        recovered_deks: set[bytes] = set()
        for blob in rotated_blobs:
            dek_hex = new_kek_cipher.decrypt(blob)
            recovered_deks.add(bytes.fromhex(dek_hex))
        assert recovered_deks == original_deks

    @pytest.mark.asyncio()
    async def test_rotate_zero_orgs_returns_zero(self) -> None:
        old_hex = os.urandom(32).hex()
        new_hex = os.urandom(32).hex()
        store = ConnectorCredentialStore(old_hex)

        db = AsyncMock()
        db.flush = AsyncMock()
        empty_result = MagicMock()
        empty_result.all = MagicMock(return_value=[])
        db.execute = AsyncMock(return_value=empty_result)

        count = await store.rotate_kek(old_kek_hex=old_hex, new_kek_hex=new_hex, db=db)
        assert count == 0
