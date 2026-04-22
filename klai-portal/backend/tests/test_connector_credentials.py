"""Re-export smoke tests for connector credential plumbing.

SPEC-CRAWLER-004 Fase 0 moved :class:`ConnectorCredentialStore` and
:data:`SENSITIVE_FIELDS` into the shared library
``klai-connector-credentials`` (see ``klai-libs/connector-credentials``). The
deep lifecycle tests (``SELECT ... FOR UPDATE``, DEK generation, KEK rotation,
tampered ciphertext) now live in that lib's own pytest suite. This file keeps
the minimum needed to prove the re-export surface from portal-api stays
stable:

- The public symbols still resolve via ``app.services.connector_credentials``
  and ``app.core.security``.
- The full encrypt/decrypt + stripped-config contract still works for every
  connector type that portal-api actually writes.
- ``ConnectorCredentialStore`` still rejects malformed KEKs.

Anything deeper belongs in
``klai-libs/connector-credentials/tests/test_store.py``.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from app.core.security import AESGCMCipher
from app.services.connector_credentials import (
    SENSITIVE_FIELDS,
    ConnectorCredentialStore,
)

# Test-only placeholder values (NOT real credentials).
FAKE_TOKEN_A = "test-placeholder-token-aaa"
FAKE_TOKEN_B = "test-placeholder-token-bbb"
FAKE_TOKEN_C = "test-placeholder-token-ccc"


def _make_store() -> ConnectorCredentialStore:
    return ConnectorCredentialStore(os.urandom(32).hex())


class TestReExportWiring:
    """The shared-lib symbols are reachable via portal-api's historical paths."""

    def test_store_is_shared_lib_class(self) -> None:
        from connector_credentials.store import (
            ConnectorCredentialStore as SharedStore,
        )

        assert ConnectorCredentialStore is SharedStore

    def test_cipher_is_shared_lib_class(self) -> None:
        from connector_credentials.cipher import AESGCMCipher as SharedCipher

        assert AESGCMCipher is SharedCipher

    def test_sensitive_fields_is_shared_lib_mapping(self) -> None:
        from connector_credentials.store import (
            SENSITIVE_FIELDS as SHARED_FIELDS,
        )

        assert SENSITIVE_FIELDS is SHARED_FIELDS


class TestSensitiveFieldsMapping:
    """SENSITIVE_FIELDS covers every connector_type portal-api writes."""

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


class TestEncryptDecryptRoundTrip:
    """End-to-end round-trip through the portal-api re-export surface."""

    @pytest.fixture()
    def db(self) -> AsyncMock:
        return AsyncMock()

    @pytest.mark.asyncio()
    async def test_github_roundtrip(self, db: AsyncMock) -> None:
        store = _make_store()
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
            assert decrypted["access_token"] == FAKE_TOKEN_A
            assert decrypted["installation_token"] == FAKE_TOKEN_B
            assert decrypted["app_private_key"] == FAKE_TOKEN_C

    @pytest.mark.asyncio()
    async def test_notion_roundtrip(self, db: AsyncMock) -> None:
        store = _make_store()
        config = {"workspace_id": "ws-123", "access_token": FAKE_TOKEN_A}
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            blob, stripped = await store.encrypt_credentials(org_id=1, connector_type="notion", config=config, db=db)
            assert "access_token" not in stripped
            assert stripped["workspace_id"] == "ws-123"
            assert blob is not None
            decrypted = await store.decrypt_credentials(org_id=1, encrypted_credentials=blob, db=db)
            assert decrypted["access_token"] == FAKE_TOKEN_A

    @pytest.mark.asyncio()
    async def test_web_crawler_roundtrip(self, db: AsyncMock) -> None:
        store = _make_store()
        config = {"url": "https://example.com", "auth_headers": FAKE_TOKEN_A}
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            blob, stripped = await store.encrypt_credentials(
                org_id=1, connector_type="web_crawler", config=config, db=db
            )
            assert "auth_headers" not in stripped
            assert stripped["url"] == "https://example.com"
            assert blob is not None
            decrypted = await store.decrypt_credentials(org_id=1, encrypted_credentials=blob, db=db)
            assert decrypted["auth_headers"] == FAKE_TOKEN_A

    @pytest.mark.asyncio()
    async def test_unknown_connector_type_no_sensitive_fields(self, db: AsyncMock) -> None:
        """Unknown connector type: nothing gets encrypted, blob is None."""
        store = _make_store()
        config = {"url": "https://example.com", "key": "value"}
        with patch.object(store, "get_or_create_dek", return_value=os.urandom(32)):
            blob, stripped = await store.encrypt_credentials(
                org_id=1, connector_type="unknown_type", config=config, db=db
            )
            assert blob is None
            assert stripped == config


class TestInvalidKeyRejection:
    """ConnectorCredentialStore rejects malformed encryption keys at construction time."""

    def test_short_hex_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="64-character"):
            ConnectorCredentialStore("abcd")

    def test_non_hex_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="hex"):
            ConnectorCredentialStore("g" * 64)

    def test_odd_length_hex_rejected(self) -> None:
        with pytest.raises(ValueError, match="64-character"):
            ConnectorCredentialStore("a" * 63)
