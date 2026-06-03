"""One-shot data migration: encrypt existing plaintext connector credentials.

Idempotent:
  - Creates per-org connector DEKs on demand
  - Merges plaintext config secrets into any existing encrypted credential blob
  - Removes sensitive fields from portal_connectors.config after encryption

Usage:
  ENCRYPTION_KEY=<64-char-hex> uv run python scripts/migrate_connector_credentials.py

Requires ENCRYPTION_KEY and DATABASE_URL env vars.
"""

import asyncio
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
    from app.models.connectors import PortalConnector
    from app.services.connector_credentials import SENSITIVE_FIELDS, ConnectorCredentialStore

    if not settings.encryption_key:
        logger.error("ENCRYPTION_KEY env var is required")
        sys.exit(1)

    store = ConnectorCredentialStore(settings.encryption_key)
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        result = await db.execute(select(PortalConnector))
        connectors = result.scalars().all()
        remediated_count = 0
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

            merged_credentials: dict = {}
            if connector.encrypted_credentials is not None:
                merged_credentials = await store.decrypt_credentials(
                    org_id=connector.org_id,
                    encrypted_credentials=bytes(connector.encrypted_credentials),
                    db=db,
                )
            merged_credentials.update(sensitive_data)
            encrypted_blob, stripped_config = await store.encrypt_credentials(
                org_id=connector.org_id,
                connector_type=connector.connector_type,
                config={**config, **merged_credentials},
                db=db,
            )
            if encrypted_blob is None:
                logger.error(
                    "Credential remediation produced no encrypted blob",
                    connector_id=str(connector.id),
                    connector_type=connector.connector_type,
                )
                sys.exit(1)
            connector.encrypted_credentials = encrypted_blob
            connector.config = stripped_config
            remediated_count += 1

            if (i + 1) % 100 == 0:
                logger.info("Migration progress", processed=i + 1, total=len(connectors))

        await db.commit()
        logger.info(
            "Migration complete",
            remediated=remediated_count,
            skipped=skipped_count,
        )


if __name__ == "__main__":
    asyncio.run(main())
