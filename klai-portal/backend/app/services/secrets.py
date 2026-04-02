"""Application-level encryption service for tenant secrets stored in PostgreSQL."""

import base64

from app.core.config import settings
from app.core.security import AESGCMCipher

_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


def is_secret_var(var_name: str) -> bool:
    """Return True if the env var name indicates a secret value (must be encrypted in DB)."""
    upper = var_name.upper()
    return any(m in upper for m in _SECRET_MARKERS)


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


def encrypt_mcp_secret(plaintext: str) -> str:
    """Encrypt een MCP secret en retourneer base64-encoded ciphertext.

    Gebruikt de bestaande PortalSecretsService (AES-256-GCM).
    Base64 encoding maakt het resultaat JSON-serializable.

    Args:
        plaintext: De te encrypten geheime waarde (API key, token, etc.).

    Returns:
        Base64-encoded ciphertext string.

    Raises:
        ValueError: Als plaintext leeg is of alleen whitespace bevat.
    """
    if not plaintext or not plaintext.strip():
        raise ValueError("MCP secret mag niet leeg zijn")
    ciphertext_bytes = portal_secrets.encrypt(plaintext)
    return base64.b64encode(ciphertext_bytes).decode("ascii")


def decrypt_mcp_secret(ciphertext: str) -> str:
    """Decrypt een base64-encoded MCP secret ciphertext.

    Args:
        ciphertext: Base64-encoded ciphertext string (output van encrypt_mcp_secret).

    Returns:
        Gedecrypteerde plaintext string.

    Raises:
        ValueError: Als de ciphertext ongeldig is of niet gedecrypt kan worden.
    """
    try:
        ciphertext_bytes = base64.b64decode(ciphertext)
        return portal_secrets.decrypt(ciphertext_bytes)
    except Exception as exc:
        raise ValueError(f"Ongeldige MCP secret ciphertext: {exc}") from exc
