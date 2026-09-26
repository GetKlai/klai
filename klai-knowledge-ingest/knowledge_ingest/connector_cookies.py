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
from collections.abc import Mapping
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


async def _load_credentials(
    connector_id: uuid.UUID,
    expected_zitadel_org_id: str,
    pool: asyncpg.Pool,
    kek_hex: str,
) -> tuple[dict[str, Any], bytes, bytes] | None:
    """Return ``(payload, encrypted_blob, dek_enc)``, or ``None`` when no credentials are stored."""
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
        return None

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
    return payload, bytes(encrypted), bytes(dek_enc)


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
    loaded = await _load_credentials(connector_id, expected_zitadel_org_id, pool, kek_hex)
    if loaded is None:
        return []
    return list(loaded[0].get("cookies") or [])


async def store_refreshed_connector_cookies(
    *,
    connector_id: uuid.UUID,
    expected_zitadel_org_id: str,
    pool: asyncpg.Pool,
    kek_hex: str,
    hostname: str,
    refreshed: Mapping[str, str],
) -> int:
    """Write session cookie values the site re-issued back into the stored credentials.

    A rolling session stays alive only if the value the site last issued is
    the one replayed next time, which is what a browser does. Only cookies
    already stored under the same name AND scoped to ``hostname`` (a stored
    domain with or without a leading dot, or no domain) are rewritten: saved
    cookies are host-scoped credentials, and a response from one host must
    never rewrite a cookie saved for another. Nothing is added or removed.

    The UPDATE is a compare-and-swap on the blob that was read, so a cookie
    paste in the portal between read and write wins and this write is
    dropped. Returns the number of cookies rewritten (0 when nothing changed
    or the swap lost). Raises the same errors as :func:`load_connector_cookies`.
    """
    loaded = await _load_credentials(connector_id, expected_zitadel_org_id, pool, kek_hex)
    if loaded is None:
        return 0
    payload, encrypted, dek_enc = loaded

    host = hostname.lower()
    changed = 0
    for cookie in payload.get("cookies") or []:
        if not isinstance(cookie, dict):
            continue
        new_value = refreshed.get(cookie.get("name") or "")
        domain = (cookie.get("domain") or host).lstrip(".").lower()
        if new_value and domain == host and new_value != cookie.get("value"):
            cookie["value"] = new_value
            changed += 1
    if not changed:
        return 0

    new_blob = ConnectorCredentialStore(kek_hex).encrypt_credentials_to_blob(payload, dek_enc)
    status = await pool.execute(
        "UPDATE portal_connectors SET encrypted_credentials = $1"
        " WHERE id = $2 AND encrypted_credentials = $3",
        new_blob,
        connector_id,
        encrypted,
    )
    return changed if status == "UPDATE 1" else 0
