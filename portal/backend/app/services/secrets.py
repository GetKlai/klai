"""Application-level encryption service for tenant secrets stored in PostgreSQL."""

from app.core.config import settings
from app.core.security import AESGCMCipher


class PortalSecretsService:
    """Encrypts and decrypts tenant secrets (OIDC client secrets, API keys).

    Uses AES-256-GCM with a key derived from the PORTAL_SECRETS_KEY env var.
    Ciphertext is stored as BYTEA in the portal_orgs table.

    Args:
        hex_key: 64-character hex string representing a 32-byte key.
    """

    def __init__(self, hex_key: str) -> None:
        self._cipher = AESGCMCipher(bytes.fromhex(hex_key))

    def encrypt(self, plaintext: str) -> bytes:
        """Encrypt a plaintext secret string.

        Args:
            plaintext: Secret string to encrypt.

        Returns:
            Nonce-prepended ciphertext bytes (suitable for BYTEA storage).
        """
        return self._cipher.encrypt(plaintext)

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt a ciphertext blob from the database.

        Args:
            ciphertext: Nonce-prepended ciphertext bytes.

        Returns:
            Decrypted plaintext string.
        """
        return self._cipher.decrypt(ciphertext)


portal_secrets = PortalSecretsService(settings.portal_secrets_key)
