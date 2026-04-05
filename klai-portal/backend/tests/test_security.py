"""Tests for AESGCMCipher in app/core/security.py.

Covers round-trip encrypt/decrypt, key validation, tampered ciphertext
detection, and wrong-key rejection.
"""

import os

import pytest
from cryptography.exceptions import InvalidTag

from app.core.security import AESGCMCipher


class TestAESGCMCipherRoundTrip:
    """Round-trip encrypt -> decrypt produces original plaintext."""

    def test_short_plaintext(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        assert cipher.decrypt(cipher.encrypt("hello")) == "hello"

    def test_empty_plaintext(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        assert cipher.decrypt(cipher.encrypt("")) == ""

    def test_long_plaintext(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        long_text = "x" * 10_000
        assert cipher.decrypt(cipher.encrypt(long_text)) == long_text

    def test_unicode_plaintext(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        text = "Klai veilige opslag \u2714"
        assert cipher.decrypt(cipher.encrypt(text)) == text

    def test_json_plaintext(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        json_str = '{"access_token": "ghp_abc123", "refresh_token": "rt_xyz"}'
        assert cipher.decrypt(cipher.encrypt(json_str)) == json_str

    def test_unique_nonce_per_encrypt(self) -> None:
        """Each encrypt call uses a fresh random nonce."""
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        c1 = cipher.encrypt("same")
        c2 = cipher.encrypt("same")
        assert c1 != c2  # different nonce => different ciphertext


class TestAESGCMCipherKeyValidation:
    """Key must be exactly 32 bytes."""

    def test_16_byte_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="32-byte key"):
            AESGCMCipher(os.urandom(16))

    def test_64_byte_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="32-byte key"):
            AESGCMCipher(os.urandom(64))

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="32-byte key"):
            AESGCMCipher(b"")


class TestAESGCMCipherTamperedCiphertext:
    """Tampered ciphertext must raise InvalidTag."""

    def test_flipped_bit_raises_invalid_tag(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        ct = bytearray(cipher.encrypt("secret"))
        ct[-1] ^= 0xFF  # flip last byte (in auth tag)
        with pytest.raises(InvalidTag):
            cipher.decrypt(bytes(ct))

    def test_truncated_ciphertext_raises(self) -> None:
        key = os.urandom(32)
        cipher = AESGCMCipher(key)
        ct = cipher.encrypt("secret")
        with pytest.raises(Exception):  # noqa: B017
            cipher.decrypt(ct[:12])  # nonce only, no ciphertext


class TestAESGCMCipherWrongKey:
    """Decrypting with a different key must fail."""

    def test_wrong_key_raises_invalid_tag(self) -> None:
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        cipher1 = AESGCMCipher(key1)
        cipher2 = AESGCMCipher(key2)
        ct = cipher1.encrypt("secret")
        with pytest.raises(InvalidTag):
            cipher2.decrypt(ct)
