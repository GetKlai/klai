"""ConnectorCredentialStore — per-org DEK wrapped under a service KEK.

Two-tier key hierarchy (SPEC-KB-020):

    ENCRYPTION_KEY env var (64-char hex = 32 bytes)
        = KEK (key encryption key, lives only in memory)
            encrypts ->
        portal_orgs.connector_dek_enc (per-org DEK, 32 bytes, BYTEA)
            encrypts ->
        portal_connectors.encrypted_credentials (JSON blob, BYTEA)

Every encrypted-data row inherits tenant isolation from the DEK: without
access to an org's DEK (which in turn requires the service's KEK) another
tenant's blob cannot be decrypted, even by a compromised db role.

The store talks to the database through the caller's
:class:`~sqlalchemy.ext.asyncio.AsyncSession` via parameterized raw SQL. It
deliberately does NOT import any service-specific ORM model so the same
library ships unchanged into portal-api, klai-connector, and knowledge-ingest.
The concrete row is always ``portal_orgs.connector_dek_enc`` and the
``SELECT ... FOR UPDATE`` lock serialises concurrent ``get_or_create_dek``
callers (SPEC-KB-020 race fix — without FOR UPDATE two callers that both see
NULL would each generate a DEK, overwrite each other, and permanently destroy
the first connector's credentials).
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from connector_credentials.cipher import AESGCMCipher

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# @MX:ANCHOR: SENSITIVE_FIELDS -- contract: changes affect every connector type
# @MX:REASON: Adding or removing a key changes what gets encrypted across all callers.
# @MX:SPEC: SPEC-KB-020, SPEC-CRAWLER-004
SENSITIVE_FIELDS: dict[str, list[str]] = {
    "github": ["access_token", "installation_token", "app_private_key"],
    "notion": ["access_token"],
    "google_drive": ["oauth_token", "refresh_token", "access_token"],
    "ms_docs": ["oauth_token", "refresh_token", "access_token"],
    "web_crawler": ["auth_headers", "cookies"],
}


# @MX:ANCHOR: ConnectorCredentialStore -- fan_in >= 3 services
# @MX:REASON: Central credential encrypt/decrypt boundary for every connector type.
# @MX:SPEC: SPEC-KB-020, SPEC-CRAWLER-004
class ConnectorCredentialStore:
    """Encrypts and decrypts connector credentials with a two-tier KEK/DEK hierarchy.

    Args:
        encryption_key_hex: 64-character hex string representing a 32-byte KEK.

    Raises:
        ValueError: if the hex string is malformed or the wrong length.
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
        """Return the plaintext DEK for ``org_id``, creating one on first use.

        Uses ``SELECT ... FOR UPDATE`` to serialise concurrent callers (two
        writers that both observed ``connector_dek_enc IS NULL`` without the
        lock would each generate a DEK, one would overwrite the other, and the
        loser's connectors would become unreadable forever).

        Raises:
            ValueError: if the org row does not exist.
        """
        row = (
            await db.execute(
                text("SELECT id, connector_dek_enc FROM portal_orgs WHERE id = :org_id FOR UPDATE"),
                {"org_id": org_id},
            )
        ).first()

        if row is None:
            raise ValueError(f"PortalOrg {org_id} not found")

        existing_enc = row.connector_dek_enc
        if existing_enc is not None:
            dek_hex = self._kek_cipher.decrypt(bytes(existing_enc))
            return bytes.fromhex(dek_hex)

        # First-time DEK generation. Row is locked — no concurrent overwrite possible.
        raw_dek = os.urandom(32)
        enc = self._kek_cipher.encrypt(raw_dek.hex())
        await db.execute(
            text("UPDATE portal_orgs SET connector_dek_enc = :enc WHERE id = :org_id"),
            {"enc": enc, "org_id": org_id},
        )
        await db.flush()
        logger.info("Generated new connector DEK", extra={"org_id": org_id})
        return raw_dek

    async def encrypt_credentials(
        self,
        org_id: int,
        connector_type: str,
        config: dict[str, Any],
        db: AsyncSession,
    ) -> tuple[bytes | None, dict[str, Any]]:
        """Extract and encrypt sensitive fields from a connector config.

        Returns:
            Tuple ``(encrypted_blob_or_None, stripped_config)``.
            The second element is ``config`` with all sensitive keys removed;
            the blob is ``None`` when nothing sensitive was present (either an
            unknown connector type or known type without the sensitive keys
            set). Callers typically persist the blob in
            ``portal_connectors.encrypted_credentials`` and the stripped
            config in ``portal_connectors.config`` / JSONB.
        """
        sensitive_keys = SENSITIVE_FIELDS.get(connector_type, [])
        if not sensitive_keys:
            return None, config

        sensitive_data: dict[str, Any] = {}
        stripped_config: dict[str, Any] = dict(config)
        for key in sensitive_keys:
            if key in stripped_config:
                sensitive_data[key] = stripped_config.pop(key)

        if not sensitive_data:
            return None, stripped_config

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
    ) -> dict[str, Any]:
        """Decrypt a blob produced by :meth:`encrypt_credentials` for ``org_id``.

        Raises:
            cryptography.exceptions.InvalidTag: if the blob was tampered with,
                encrypted for a different org, or encrypted with a different
                KEK/DEK.
        """
        dek = await self.get_or_create_dek(org_id, db)
        dek_cipher = AESGCMCipher(dek)
        plaintext_json = dek_cipher.decrypt(encrypted_credentials)
        return json.loads(plaintext_json)

    def decrypt_credentials_from_blobs(
        self,
        encrypted_credentials: bytes,
        connector_dek_enc: bytes,
    ) -> dict[str, Any]:
        """Decrypt a credentials blob given both the encrypted DEK and payload.

        This is the DB-driver-agnostic sibling of :meth:`decrypt_credentials`.
        The caller fetches both ``portal_orgs.connector_dek_enc`` and
        ``portal_connectors.encrypted_credentials`` via whatever driver they
        use (e.g. asyncpg.Pool in knowledge-ingest) and hands the bytes over
        to this method. No SQLAlchemy session required.

        Raises:
            cryptography.exceptions.InvalidTag: if either blob was tampered
                with or encrypted under a different key.
        """
        dek_hex = self._kek_cipher.decrypt(connector_dek_enc)
        dek_cipher = AESGCMCipher(bytes.fromhex(dek_hex))
        plaintext_json = dek_cipher.decrypt(encrypted_credentials)
        return json.loads(plaintext_json)

    # @MX:WARN: rotate_kek -- wrong invocation = permanent data loss
    # @MX:REASON: Passing the wrong old_kek_hex makes every DEK unreadable under the new KEK.
    async def rotate_kek(
        self,
        old_kek_hex: str,
        new_kek_hex: str,
        db: AsyncSession,
    ) -> int:
        """Re-encrypt every org's DEK under ``new_kek_hex``.

        This is a cross-org system operation and intentionally bypasses per-org
        scoping — a partial rotation would leave some orgs unreadable under
        the new KEK. Invoke only via an admin-level code path; never from a
        user-scoped request.

        Returns:
            Number of rows rotated.
        """
        old_cipher = AESGCMCipher(bytes.fromhex(old_kek_hex))
        new_cipher = AESGCMCipher(bytes.fromhex(new_kek_hex))

        result = await db.execute(
            text("SELECT id, connector_dek_enc FROM portal_orgs WHERE connector_dek_enc IS NOT NULL")
        )
        rows = result.all()

        count = 0
        for row in rows:
            dek_hex = old_cipher.decrypt(bytes(row.connector_dek_enc))
            new_enc = new_cipher.encrypt(dek_hex)
            await db.execute(
                text("UPDATE portal_orgs SET connector_dek_enc = :enc WHERE id = :id"),
                {"enc": new_enc, "id": row.id},
            )
            count += 1

        await db.flush()
        logger.info("KEK rotation complete", extra={"rotated_count": count})
        return count
