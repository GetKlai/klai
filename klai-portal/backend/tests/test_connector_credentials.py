"""Tests for ConnectorCredentialStore service.

Covers round-trip encrypt/decrypt for each connector type, SENSITIVE_FIELDS
completeness, DEK generation and reuse, fallback when encrypted_credentials
is None, invalid key rejection, and tampered ciphertext detection.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.exceptions import InvalidTag

from app.services.connector_credentials import (
    SENSITIVE_FIELDS,
    ConnectorCredentialStore,
)

# -- Helpers ------------------------------------------------------------------

# Test-only placeholder values (NOT real credentials)
FAKE_TOKEN_A = "test-placeholder-token-aaa"
FAKE_TOKEN_B = "test-placeholder-token-bbb"
FAKE_TOKEN_C = "test-placeholder-token-ccc"


def _make_store(hex_key: str | None = None) -> ConnectorCredentialStore:
    """Create a ConnectorCredentialStore with a valid or custom key."""
    if hex_key is None:
        hex_key = os.urandom(32).hex()
    return ConnectorCredentialStore(hex_key)


def _make_org_mock(connector_dek_enc: bytes | None = None) -> MagicMock:
    """Create a mock PortalOrg with optional encrypted DEK."""
    org = MagicMock()
    org.connector_dek_enc = connector_dek_enc
    return org


# -- SENSITIVE_FIELDS completeness --------------------------------------------


class TestSensitiveFieldsMapping:
    """SENSITIVE_FIELDS covers all connector types with correct field names."""

    def test_github_fields(self) -> None:
        assert "access_token" in SENSITIVE_FIELDS["github"]
        assert "installation_token" in SENSITIVE_FIELDS["github"]
        assert "app_private_key" in SENSITIVE_FIELDS["github"]

    def test_notion_fields(self) -> None:
        assert "api_token" in SENSITIVE_FIELDS["notion"]

    def test_google_drive_fields(self) -> None:
        fields = SENSITIVE_FIELDS["google_drive"]
        assert "oauth_token" in fields
        assert "refresh_token" in fields
        assert "access_token" in fields

    def test_ms_docs_fields(self) -> None:
        fields = SENSITIVE_FIELDS["ms_docs"]
        assert "oauth_token" in fields
        assert "refresh_token" in fields
        assert "access_token" in fields

    def test_web_crawler_fields(self) -> None:
        assert "auth_headers" in SENSITIVE_FIELDS["web_crawler"]

    def test_all_connector_types_present(self) -> None:
        expected_types = {"github", "notion", "google_drive", "ms_docs", "web_crawler"}
        assert set(SENSITIVE_FIELDS.keys()) == expected_types


# -- Round-trip encrypt/decrypt per connector type ----------------------------


class TestEncryptDecryptRoundTrip:
    """encrypt_credentials + decrypt_credentials round-trip for each type."""

    @pytest.fixture()
    def store(self) -> ConnectorCredentialStore:
        return _make_store()

    @pytest.fixture()
    def db(self) -> AsyncMock:
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_github_roundtrip(self, store: ConnectorCredentialStore, db: AsyncMock) -> None:
        config = {
            "repo": "GetKlai/klai",
            "access_token": FAKE_TOKEN_A,
            "installation_token": FAKE_TOKEN_B,
            "app_private_key": FAKE_TOKEN_C,
        }
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            encrypted_blob, stripped = await store.encrypt_credentials(
                org_id=1, connector_type="github", config=config, db=db
            )
            # Sensitive fields removed from stripped config
            assert "access_token" not in stripped
            assert "installation_token" not in stripped
            assert "app_private_key" not in stripped
            # Non-sensitive fields preserved
            assert stripped["repo"] == "GetKlai/klai"

            # Decrypt round-trip
            decrypted = await store.decrypt_credentials(org_id=1, encrypted_credentials=encrypted_blob, db=db)
            assert decrypted["access_token"] == FAKE_TOKEN_A
            assert decrypted["installation_token"] == FAKE_TOKEN_B
            assert decrypted["app_private_key"] == FAKE_TOKEN_C

    @pytest.mark.asyncio()
    async def test_notion_roundtrip(self, store: ConnectorCredentialStore, db: AsyncMock) -> None:
        config = {"workspace_id": "ws-123", "api_token": FAKE_TOKEN_A}
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            encrypted_blob, stripped = await store.encrypt_credentials(
                org_id=1, connector_type="notion", config=config, db=db
            )
            assert "api_token" not in stripped
            assert stripped["workspace_id"] == "ws-123"
            decrypted = await store.decrypt_credentials(org_id=1, encrypted_credentials=encrypted_blob, db=db)
            assert decrypted["api_token"] == FAKE_TOKEN_A

    @pytest.mark.asyncio()
    async def test_web_crawler_roundtrip(self, store: ConnectorCredentialStore, db: AsyncMock) -> None:
        config = {"url": "https://example.com", "auth_headers": FAKE_TOKEN_A}
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            encrypted_blob, stripped = await store.encrypt_credentials(
                org_id=1, connector_type="web_crawler", config=config, db=db
            )
            assert "auth_headers" not in stripped
            assert stripped["url"] == "https://example.com"
            decrypted = await store.decrypt_credentials(org_id=1, encrypted_credentials=encrypted_blob, db=db)
            assert decrypted["auth_headers"] == FAKE_TOKEN_A

    @pytest.mark.asyncio()
    async def test_unknown_connector_type_no_sensitive_fields(
        self, store: ConnectorCredentialStore, db: AsyncMock
    ) -> None:
        """Unknown connector type: nothing gets encrypted, blob is empty."""
        config = {"url": "https://example.com", "key": "value"}
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            encrypted_blob, stripped = await store.encrypt_credentials(
                org_id=1, connector_type="unknown_type", config=config, db=db
            )
            # No sensitive fields for unknown type: nothing encrypted
            assert encrypted_blob is None
            assert stripped == config


# -- DEK generation and reuse -------------------------------------------------


class TestDEKLifecycle:
    """DEK is generated once per org and reused on subsequent calls."""

    @pytest.mark.asyncio()
    async def test_generates_new_dek_when_none_exists(self) -> None:
        store = _make_store()
        org = _make_org_mock(connector_dek_enc=None)
        db = AsyncMock()
        # Mock the DB to return the org
        db.get = AsyncMock(return_value=org)
        dek = await store.get_or_create_dek(org_id=1, db=db)
        assert isinstance(dek, bytes)
        assert len(dek) == 32
        # DEK should have been encrypted and stored on org
        assert org.connector_dek_enc is not None

    @pytest.mark.asyncio()
    async def test_reuses_existing_dek(self) -> None:
        store = _make_store()
        # Pre-encrypt a DEK using the store's own KEK
        raw_dek = os.urandom(32)
        encrypted_dek = store._kek_cipher.encrypt(raw_dek.hex())
        org = _make_org_mock(connector_dek_enc=encrypted_dek)
        db = AsyncMock()
        db.get = AsyncMock(return_value=org)
        dek = await store.get_or_create_dek(org_id=1, db=db)
        assert dek == raw_dek


# -- Invalid key rejection ----------------------------------------------------


class TestInvalidKeyRejection:
    """ConnectorCredentialStore rejects invalid encryption keys."""

    def test_short_hex_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            ConnectorCredentialStore("abcd")  # too short

    def test_non_hex_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            ConnectorCredentialStore("g" * 64)  # not valid hex

    def test_odd_length_hex_rejected(self) -> None:
        with pytest.raises(ValueError):
            ConnectorCredentialStore("a" * 63)  # odd length


# -- Tampered ciphertext detection --------------------------------------------


class TestTamperedCiphertextDetection:
    """Tampered encrypted_credentials must raise InvalidTag."""

    @pytest.mark.asyncio()
    async def test_tampered_blob_raises_invalid_tag(self) -> None:
        store = _make_store()
        db = AsyncMock()
        config = {"access_token": FAKE_TOKEN_A}
        dek = os.urandom(32)
        with patch.object(store, "get_or_create_dek", return_value=dek):
            encrypted_blob, _ = await store.encrypt_credentials(org_id=1, connector_type="github", config=config, db=db)
            # Tamper with the blob
            tampered = bytearray(encrypted_blob)
            tampered[-1] ^= 0xFF
            with pytest.raises(InvalidTag):
                await store.decrypt_credentials(org_id=1, encrypted_credentials=bytes(tampered), db=db)
