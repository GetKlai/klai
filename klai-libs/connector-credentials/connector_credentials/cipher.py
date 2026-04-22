"""AES-256-GCM authenticated encryption primitive.

Ciphertext wire format: ``nonce (12 bytes) || ciphertext``. The GCM
authentication tag is appended automatically by the underlying
:class:`cryptography.hazmat.primitives.ciphers.aead.AESGCM`.

This module is a pure primitive — no key-management, no persistence.
Higher-level callers (see :class:`connector_credentials.store.ConnectorCredentialStore`)
handle the KEK/DEK hierarchy.
"""

from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class AESGCMCipher:
    """AES-256-GCM cipher.

    Args:
        key: 32-byte AES-256 key.

    Raises:
        ValueError: if ``key`` is not exactly 32 bytes.
    """

    _NONCE_SIZE = 12

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError(f"AES-256 requires a 32-byte key, got {len(key)} bytes")
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a UTF-8 string.

        A fresh 12-byte nonce is generated per call via :func:`os.urandom`.
        Reusing a nonce under the same key is catastrophic for GCM — do not
        supply your own nonce unless you fully understand the consequences.
        """
        nonce = os.urandom(self._NONCE_SIZE)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return nonce + ct

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt a blob produced by :meth:`encrypt`.

        Raises:
            cryptography.exceptions.InvalidTag: if the blob was tampered with
                or encrypted under a different key.
        """
        nonce = ciphertext[: self._NONCE_SIZE]
        ct = ciphertext[self._NONCE_SIZE :]
        return self._aes.decrypt(nonce, ct, None).decode("utf-8")
