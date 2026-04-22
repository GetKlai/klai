"""Tests for AESGCMCipher — the AES-256-GCM primitive."""

from __future__ import annotations

import os

import pytest
from cryptography.exceptions import InvalidTag

from connector_credentials import AESGCMCipher


class TestKeyValidation:
    """AESGCMCipher rejects non-32-byte keys."""

    def test_accepts_32_byte_key(self) -> None:
        AESGCMCipher(os.urandom(32))

    def test_rejects_16_byte_key(self) -> None:
        with pytest.raises(ValueError, match="32-byte"):
            AESGCMCipher(os.urandom(16))

    def test_rejects_empty_key(self) -> None:
        with pytest.raises(ValueError, match="32-byte"):
            AESGCMCipher(b"")

    def test_rejects_33_byte_key(self) -> None:
        with pytest.raises(ValueError, match="32-byte"):
            AESGCMCipher(os.urandom(33))


class TestRoundTrip:
    """encrypt/decrypt round-trip preserves plaintext."""

    def test_simple_string(self) -> None:
        cipher = AESGCMCipher(os.urandom(32))
        blob = cipher.encrypt("hello world")
        assert cipher.decrypt(blob) == "hello world"

    def test_unicode_string(self) -> None:
        cipher = AESGCMCipher(os.urandom(32))
        plaintext = "café éè 你好 \U0001f600"
        assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext

    def test_empty_string(self) -> None:
        cipher = AESGCMCipher(os.urandom(32))
        assert cipher.decrypt(cipher.encrypt("")) == ""

    def test_large_string(self) -> None:
        cipher = AESGCMCipher(os.urandom(32))
        plaintext = "A" * 10000
        assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext


class TestWireFormat:
    """Ciphertext format is ``nonce (12 bytes) || ciphertext``."""

    def test_output_starts_with_12_byte_nonce(self) -> None:
        cipher = AESGCMCipher(os.urandom(32))
        blob = cipher.encrypt("payload")
        # Nonce is 12 bytes; GCM adds 16-byte auth tag; payload is 7 bytes.
        assert len(blob) == 12 + 7 + 16

    def test_different_nonces_per_call(self) -> None:
        """GCM without a fresh nonce per call is catastrophic; verify randomness."""
        cipher = AESGCMCipher(os.urandom(32))
        blob1 = cipher.encrypt("same-plaintext")
        blob2 = cipher.encrypt("same-plaintext")
        assert blob1 != blob2  # nonce randomness → different ciphertext


class TestTampering:
    """Modified ciphertext or wrong-key decrypt raises InvalidTag."""

    def test_tampered_tag_raises(self) -> None:
        cipher = AESGCMCipher(os.urandom(32))
        blob = bytearray(cipher.encrypt("secret"))
        blob[-1] ^= 0xFF
        with pytest.raises(InvalidTag):
            cipher.decrypt(bytes(blob))

    def test_wrong_key_raises(self) -> None:
        sender = AESGCMCipher(os.urandom(32))
        receiver = AESGCMCipher(os.urandom(32))
        blob = sender.encrypt("secret")
        with pytest.raises(InvalidTag):
            receiver.decrypt(blob)
