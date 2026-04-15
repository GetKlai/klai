"""Connector credential encryption service (SPEC-KB-020).

Implements a two-tier key hierarchy:
  KEK (from ENCRYPTION_KEY env var, 32 bytes)
    -> encrypts per-tenant DEK (stored as connector_dek_enc BYTEA on portal_orgs)
      -> encrypts sensitive connector fields (stored as encrypted_credentials BYTEA)

All encryption uses AESGCMCipher (AES-256-GCM, nonce || ciphertext, raw bytes to BYTEA).
"""

import json
import os

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import AESGCMCipher
from app.models.portal import PortalOrg

logger = structlog.get_logger()

# @MX:ANCHOR: [AUTO] SENSITIVE_FIELDS -- contract: changes affect all connector types
# @MX:REASON: [AUTO] Adding/removing fields changes what gets encrypted for every connector
SENSITIVE_FIELDS: dict[str, list[str]] = {
    "github": ["access_token", "installation_token", "app_private_key"],
    "notion": ["access_token"],
    "google_drive": ["oauth_token", "refresh_token", "access_token"],
    "ms_docs": ["oauth_token", "refresh_token", "access_token"],
    "web_crawler": ["auth_headers", "cookies"],
}


# @MX:ANCHOR: [AUTO] ConnectorCredentialStore -- fan_in >= 3 (API endpoints, migration, tests)
# @MX:REASON: [AUTO] Central service for all credential encrypt/decrypt operations
class ConnectorCredentialStore:
    """Encrypts and decrypts connector credentials using a two-tier key hierarchy.

    Args:
        encryption_key_hex: 64-character hex string representing a 32-byte KEK.
    """

    def __init__(self, encryption_key_hex: str) -> None:
        if len(encryption_key_hex) != 64:
            raise ValueError(f"ENCRYPTION_KEY must be a 64-character hex string, got {len(encryption_key_hex)} chars")
        try:
            kek_bytes = bytes.fromhex(encryption_key_hex)
        except ValueError as exc:
            raise ValueError("ENCRYPTION_KEY must be valid hexadecimal") from exc
        self._kek_cipher = AESGCMCipher(kek_bytes)

    async def get_or_create_dek(self, org_id: int, db: AsyncSession) -> bytes:
        """Return the plaintext DEK for an org, creating one if needed.

        Uses SELECT ... FOR UPDATE to prevent a race condition where two concurrent
        requests both see connector_dek_enc IS NULL, generate different DEKs, and one
        overwrites the other (making the first connector's credentials unreadable).

        Args:
            org_id: The portal org ID.
            db: Async database session.

        Returns:
            32-byte plaintext DEK.
        """
        result = await db.execute(select(PortalOrg).where(PortalOrg.id == org_id).with_for_update())
        org = result.scalar_one_or_none()
        if org is None:
            raise ValueError(f"PortalOrg {org_id} not found")

        if org.connector_dek_enc is not None:
            # Decrypt existing DEK
            dek_hex = self._kek_cipher.decrypt(org.connector_dek_enc)
            return bytes.fromhex(dek_hex)

        # Generate new DEK — row is locked so no concurrent writer can overwrite it
        raw_dek = os.urandom(32)
        org.connector_dek_enc = self._kek_cipher.encrypt(raw_dek.hex())
        await db.flush()
        logger.info("Generated new connector DEK", org_id=org_id)
        return raw_dek

    async def encrypt_credentials(
        self,
        org_id: int,
        connector_type: str,
        config: dict,
        db: AsyncSession,
    ) -> tuple[bytes | None, dict]:
        """Extract and encrypt sensitive fields from connector config.

        Args:
            org_id: The portal org ID.
            connector_type: Connector type string (e.g. "github").
            config: Full connector config dict.
            db: Async database session.

        Returns:
            Tuple of (encrypted_blob_or_None, stripped_config).
            If no sensitive fields exist for this connector type, returns (None, config).
        """
        sensitive_keys = SENSITIVE_FIELDS.get(connector_type, [])
        if not sensitive_keys:
            return None, config

        # Extract sensitive fields
        sensitive_data: dict[str, str] = {}
        stripped_config = dict(config)
        for key in sensitive_keys:
            if key in stripped_config:
                sensitive_data[key] = stripped_config.pop(key)

        if not sensitive_data:
            return None, stripped_config

        # Encrypt sensitive fields
        dek = await self.get_or_create_dek(org_id, db)
        dek_cipher = AESGCMCipher(dek)
        plaintext_json = json.dumps(sensitive_data)
        encrypted_blob = dek_cipher.encrypt(plaintext_json)
        return encrypted_blob, stripped_config

    async def decrypt_credentials(
        self,
        org_id: int,
        encrypted_credentials: bytes,
        db: AsyncSession,
    ) -> dict:
        """Decrypt an encrypted credentials blob.

        Args:
            org_id: The portal org ID.
            encrypted_credentials: BYTEA blob from portal_connectors.
            db: Async database session.

        Returns:
            Dict of decrypted sensitive fields.
        """
        dek = await self.get_or_create_dek(org_id, db)
        dek_cipher = AESGCMCipher(dek)
        plaintext_json = dek_cipher.decrypt(encrypted_credentials)
        return json.loads(plaintext_json)

    # @MX:WARN: [AUTO] rotate_kek -- dangerous: wrong execution = permanent data loss
    # @MX:REASON: [AUTO] Incorrectly calling this with wrong keys makes all DEKs unreadable
    async def rotate_kek(
        self,
        old_kek_hex: str,
        new_kek_hex: str,
        db: AsyncSession,
    ) -> int:
        """Re-encrypt all connector DEKs with a new KEK.

        Args:
            old_kek_hex: Current 64-char hex KEK.
            new_kek_hex: New 64-char hex KEK.
            db: Async database session.

        Returns:
            Number of DEKs rotated.
        """
        old_cipher = AESGCMCipher(bytes.fromhex(old_kek_hex))
        new_cipher = AESGCMCipher(bytes.fromhex(new_kek_hex))

        result = await db.execute(select(PortalOrg).where(PortalOrg.connector_dek_enc.isnot(None)))
        orgs = result.scalars().all()
        count = 0
        for org in orgs:
            assert org.connector_dek_enc is not None  # guaranteed by isnot(None) filter
            dek_hex = old_cipher.decrypt(org.connector_dek_enc)
            org.connector_dek_enc = new_cipher.encrypt(dek_hex)
            count += 1
        await db.flush()
        logger.info("KEK rotation complete", rotated_count=count)
        return count


def _create_credential_store() -> ConnectorCredentialStore | None:
    """Create the module-level credential store singleton.

    Returns None if ENCRYPTION_KEY is not configured (e.g. dev environments
    that don't need connector credential encryption).
    """
    from app.core.config import settings

    if not settings.encryption_key:
        return None
    try:
        return ConnectorCredentialStore(settings.encryption_key)
    except ValueError:
        logger.warning("Invalid ENCRYPTION_KEY, connector credential encryption disabled")
        return None


# Module-level singleton -- None when encryption is not configured
credential_store = _create_credential_store()
