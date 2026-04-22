"""Helper for loading connector cookies via the shared credentials lib.

Extracted from ``routes.crawl_sync`` so the Procrastinate ``run_crawl`` task
can reload cookies at execution time (not at enqueue time). Keeping cookies
out of the task payload means they are never written to Procrastinate's
``procrastinate_jobs.args`` column or logged in
``procrastinate.worker:Starting job ...(cookies=[...])`` lines — a REQ-05.4
compliance requirement.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from connector_credentials import ConnectorCredentialStore
from cryptography.exceptions import InvalidTag

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger()


class ConnectorNotFoundError(ValueError):
    """The ``portal_connectors`` row for the given id is missing."""


class ConnectorOrgMismatchError(ValueError):
    """The zitadel_org_id on portal_orgs does not match the caller's org_id."""


class ConnectorDecryptError(ValueError):
    """The cookies blob could not be decrypted (tampering or wrong KEK)."""


async def load_connector_cookies(
    *,
    connector_id: uuid.UUID,
    expected_zitadel_org_id: str,
    pool: asyncpg.Pool,
    kek_hex: str,
) -> list[dict[str, Any]]:
    """Return the plaintext cookies list for a connector.

    The function is DB-driver-specific (asyncpg) on purpose so it can run
    both inside a FastAPI request handler and inside a Procrastinate task
    without needing a SQLAlchemy session.

    Raises:
        ConnectorNotFoundError: no row with that connector_id.
        ConnectorOrgMismatchError: connector belongs to a different tenant.
        ConnectorDecryptError: blob tampered or encrypted under a different KEK.
        ValueError: kek_hex is empty or malformed.
    """
    if not kek_hex:
        raise ValueError("encryption_key_not_configured")

    row = await pool.fetchrow(
        """
        SELECT c.id,
               c.encrypted_credentials,
               o.zitadel_org_id,
               o.connector_dek_enc
        FROM portal_connectors c
        JOIN portal_orgs o ON o.id = c.org_id
        WHERE c.id = $1
        """,
        connector_id,
    )
    if row is None:
        raise ConnectorNotFoundError(f"connector {connector_id} not found")

    if str(row["zitadel_org_id"]) != str(expected_zitadel_org_id):
        raise ConnectorOrgMismatchError(
            f"connector {connector_id} belongs to org "
            f"{row['zitadel_org_id']}, not {expected_zitadel_org_id}",
        )

    encrypted = row["encrypted_credentials"]
    dek_enc = row["connector_dek_enc"]
    if not encrypted or not dek_enc:
        return []

    store = ConnectorCredentialStore(kek_hex)
    try:
        payload = store.decrypt_credentials_from_blobs(
            encrypted_credentials=bytes(encrypted),
            connector_dek_enc=bytes(dek_enc),
        )
    except InvalidTag as exc:
        raise ConnectorDecryptError(
            f"decrypt failed for connector {connector_id}",
        ) from exc

    cookies = payload.get("cookies") or []
    return list(cookies)
