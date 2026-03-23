"""Credential encryption service with SecretsStore Protocol abstraction."""

from typing import Protocol, runtime_checkable

from app.core.security import AESGCMCipher


@runtime_checkable
class SecretsStore(Protocol):
    """Protocol for credential storage backends.

    The first implementation uses PostgreSQL + AES-256-GCM.
    The interface is swappable to Infisical/Vault without
    changing the rest of the codebase.
    """

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a plaintext string. Returns ciphertext with nonce prepended."""
        ...

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt a ciphertext blob. Expects nonce prepended to ciphertext."""
        ...


class PostgresSecretsStore:
    """SecretsStore implementation using AES-256-GCM via :class:`AESGCMCipher`.

    Credentials are encrypted in memory and stored as ``BYTEA`` in PostgreSQL.
    The encryption key is loaded from the ``ENCRYPTION_KEY`` environment variable.

    Args:
        cipher: Initialised :class:`AESGCMCipher` instance.
    """

    def __init__(self, cipher: AESGCMCipher) -> None:
        self._cipher = cipher

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a plaintext credential string.

        Args:
            plaintext: Secret string to encrypt.

        Returns:
            Nonce-prepended ciphertext bytes.
        """
        return self._cipher.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt a ciphertext credential blob.

        Args:
            ciphertext: Nonce-prepended ciphertext bytes.

        Returns:
            Decrypted plaintext string.
        """
        return self._cipher.decrypt(ciphertext)
