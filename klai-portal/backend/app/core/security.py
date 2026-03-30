"""AES-256-GCM encryption utilities for credential storage."""

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AESGCMCipher:
    """AES-256-GCM authenticated encryption cipher.

    The ciphertext format is ``nonce (12 bytes) || ciphertext``.

    Args:
        key: 32-byte AES-256 key.
    """

    _NONCE_SIZE = 12

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError(f"AES-256 requires a 32-byte key, got {len(key)} bytes")
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a plaintext string.

        Args:
            plaintext: UTF-8 string to encrypt.

        Returns:
            Nonce prepended to ciphertext bytes.
        """
        nonce = os.urandom(self._NONCE_SIZE)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ct

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt ciphertext produced by :meth:`encrypt`.

        Args:
            ciphertext: Nonce-prepended ciphertext bytes.

        Returns:
            Decrypted UTF-8 string.
        """
        nonce = ciphertext[: self._NONCE_SIZE]
        ct = ciphertext[self._NONCE_SIZE :]
        return self._aes.decrypt(nonce, ct, None).decode("utf-8")
