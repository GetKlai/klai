"""One-shot data migration: encrypt existing plaintext connector credentials.

Idempotent:
  - Skips orgs where connector_dek_enc IS NOT NULL
  - Skips connectors where encrypted_credentials IS NOT NULL
  - Does NOT remove sensitive fields from config (backward compat)

Usage:
  ENCRYPTION_KEY=<64-char-hex> uv run python scripts/migrate_connector_credentials.py

Requires ENCRYPTION_KEY and DATABASE_URL env vars.
"""

import asyncio
import json
import os
import sys

# Ensure the app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog

logger = structlog.get_logger()


async def main() -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings
    from app.core.security import AESGCMCipher
    from app.models.connectors import PortalConnector
    from app.models.portal import PortalOrg
    from app.services.connector_credentials import SENSITIVE_FIELDS

    if not settings.encryption_key:
        logger.error("ENCRYPTION_KEY env var is required")
        sys.exit(1)

    kek = AESGCMCipher(bytes.fromhex(settings.encryption_key))
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Step 1: Generate DEKs for orgs that don't have one yet
        result = await db.execute(
            select(PortalOrg).where(PortalOrg.connector_dek_enc.is_(None))
        )
        orgs_without_dek = result.scalars().all()
        dek_count = 0
        dek_cache: dict[int, bytes] = {}

        for org in orgs_without_dek:
            raw_dek = os.urandom(32)
            org.connector_dek_enc = kek.encrypt(raw_dek.hex())
            dek_cache[org.id] = raw_dek
            dek_count += 1

        if dek_count:
            logger.info("Generated DEKs for orgs", count=dek_count)
            await db.flush()

        # Pre-load existing DEKs
        result = await db.execute(
            select(PortalOrg).where(PortalOrg.connector_dek_enc.isnot(None))
        )
        for org in result.scalars().all():
            if org.id not in dek_cache:
                dek_hex = kek.decrypt(org.connector_dek_enc)
                dek_cache[org.id] = bytes.fromhex(dek_hex)

        # Step 2: Encrypt connector credentials
        result = await db.execute(
            select(PortalConnector).where(PortalConnector.encrypted_credentials.is_(None))
        )
        connectors = result.scalars().all()
        encrypted_count = 0
        skipped_count = 0

        for i, connector in enumerate(connectors):
            sensitive_keys = SENSITIVE_FIELDS.get(connector.connector_type, [])
            if not sensitive_keys:
                skipped_count += 1
                continue

            config = connector.config or {}
            sensitive_data = {k: config[k] for k in sensitive_keys if k in config}
            if not sensitive_data:
                skipped_count += 1
                continue

            org_dek = dek_cache.get(connector.org_id)
            if org_dek is None:
                logger.warning(
                    "No DEK for org, skipping connector",
                    org_id=connector.org_id,
                    connector_id=str(connector.id),
                )
                skipped_count += 1
                continue

            dek_cipher = AESGCMCipher(org_dek)
            connector.encrypted_credentials = dek_cipher.encrypt(json.dumps(sensitive_data))
            encrypted_count += 1

            # NOTE: We intentionally do NOT remove sensitive fields from
            # connector.config yet. This preserves backward compatibility
            # until all consumers use the decrypt path.

            if (i + 1) % 100 == 0:
                logger.info("Migration progress", processed=i + 1, total=len(connectors))

        await db.commit()
        logger.info(
            "Migration complete",
            encrypted=encrypted_count,
            skipped=skipped_count,
            deks_generated=dek_count,
        )


if __name__ == "__main__":
    asyncio.run(main())
