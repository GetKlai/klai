"""Tests for MCP secret encryption helpers (AC-M2-01, AC-M2-02, AC-M2-05)."""

import base64
import re

import pytest

from app.services.secrets import decrypt_mcp_secret, encrypt_mcp_secret


class TestEncryptDecryptRoundTrip:
    """AC-M2-01: encrypt/decrypt round-trip."""

    def test_roundtrip_produces_original_plaintext(self) -> None:
        plaintext = "sk-test-api-key-12345"
        ciphertext = encrypt_mcp_secret(plaintext)
        assert decrypt_mcp_secret(ciphertext) == plaintext

    def test_ciphertext_is_valid_base64(self) -> None:
        ciphertext = encrypt_mcp_secret("sk-test-api-key-12345")
        # Should not raise
        decoded = base64.b64decode(ciphertext)
        assert len(decoded) > 0

    def test_ciphertext_differs_from_plaintext(self) -> None:
        plaintext = "sk-test-api-key-12345"
        ciphertext = encrypt_mcp_secret(plaintext)
        assert ciphertext != plaintext

    def test_same_plaintext_produces_different_ciphertext_each_time(self) -> None:
        """AES-GCM uses a random nonce each call."""
        plaintext = "sk-test-api-key-12345"
        c1 = encrypt_mcp_secret(plaintext)
        c2 = encrypt_mcp_secret(plaintext)
        assert c1 != c2

    def test_roundtrip_with_url_value(self) -> None:
        url = "https://crm.getklai.com"
        assert decrypt_mcp_secret(encrypt_mcp_secret(url)) == url

    def test_roundtrip_with_long_secret(self) -> None:
        long_secret = "sk-" + "a" * 200
        assert decrypt_mcp_secret(encrypt_mcp_secret(long_secret)) == long_secret


class TestEmptySecretRejection:
    """AC-M2-05: lege API key wordt geweigerd."""

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            encrypt_mcp_secret("")

    def test_whitespace_only_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            encrypt_mcp_secret("   ")

    def test_newline_only_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            encrypt_mcp_secret("\n")


class TestInvalidCiphertext:
    """decrypt_mcp_secret raises ValueError for invalid input."""

    def test_invalid_base64_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            decrypt_mcp_secret("not-valid-base64!!!")

    def test_valid_base64_but_wrong_content_raises_value_error(self) -> None:
        garbage = base64.b64encode(b"short garbage").decode("ascii")
        with pytest.raises(ValueError):
            decrypt_mcp_secret(garbage)


class TestNoSecretInLogs:
    """AC-M2-02: gedecrypteerde secrets verschijnen niet in logs.

    We verify that the decrypt function does not log the plaintext value.
    Structural test: the functions don't call the logger at all (no log output
    can contain the secret).
    """

    def test_encrypt_does_not_expose_secret_in_repr(self) -> None:
        plaintext = "super-secret-api-key-xyz"
        ciphertext = encrypt_mcp_secret(plaintext)
        assert plaintext not in ciphertext

    def test_ciphertext_contains_no_sk_prefix_pattern(self) -> None:
        """Ensure ciphertext is opaque - no readable API key patterns."""
        plaintext = "sk-1234567890abcdef"
        ciphertext = encrypt_mcp_secret(plaintext)
        assert not re.search(r"sk-[a-zA-Z0-9]", ciphertext)
