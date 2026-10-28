"""Helper for loading connector cookies via the shared credentials lib.

Extracted from ``routes.crawl_sync`` so the Procrastinate ``run_crawl`` task
can reload cookies at execution time (not at enqueue time). Keeping cookies
out of the task payload means they are never written to Procrastinate's
``procrastinate_jobs.args`` column or logged in
``procrastinate.worker:Starting job ...(cookies=[...])`` lines — a REQ-05.4
compliance requirement.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
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


@dataclass(frozen=True)
class StoredCredentials:
    """A connector's decrypted credentials together with the exact blob they came from.

    ``encrypted`` is kept so a later write can be a compare-and-swap on the
    very blob this payload was read from.
    """

    payload: dict[str, Any]
    encrypted: bytes
    dek_enc: bytes
    org_id: int


async def load_connector_credentials(
    *,
    connector_id: uuid.UUID,
    expected_zitadel_org_id: str,
    pool: asyncpg.Pool,
    kek_hex: str,
) -> StoredCredentials | None:
    """Return the connector's stored credentials, or ``None`` when none are stored.

    Raises the same errors as :func:`load_connector_cookies`.
    """
    if not kek_hex:
        raise ValueError("encryption_key_not_configured")

    row = await pool.fetchrow(
        """
        SELECT c.id,
               c.org_id,
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
    return StoredCredentials(
        payload=payload,
        encrypted=bytes(encrypted),
        dek_enc=bytes(dek_enc),
        org_id=row["org_id"],
    )


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
    stored = await load_connector_credentials(
        connector_id=connector_id,
        expected_zitadel_org_id=expected_zitadel_org_id,
        pool=pool,
        kek_hex=kek_hex,
    )
    if stored is None:
        return []
    return list(stored.payload.get("cookies") or [])


class CookieJar:
    """A connector's stored cookies, kept per host the way a browser keeps them.

    Each cookie is a dict with ``name, value, domain, path, expires`` (ISO or
    null), ``secure`` and ``httpOnly``. A cookie without a domain (an older
    row) belongs to ``base_host``, the connector's own host. A domain with a
    leading dot also covers subdomains; one without covers that exact host
    only, which is how the portal has always stored a pasted cookie.
    """

    def __init__(self, cookies: list[Any], base_host: str) -> None:
        self.cookies: list[dict[str, Any]] = copy.deepcopy(
            [c for c in cookies if isinstance(c, dict)]
        )
        self.base_host = base_host.lower()
        # Hosts whose Set-Cookie changed the jar; non-empty means there is
        # something to persist.
        self.issued_hosts: set[str] = set()

    def _domain(self, cookie: dict[str, Any]) -> str:
        return str(cookie.get("domain") or self.base_host).lower()

    def jar_for_host(self, host: str, path: str = "/") -> dict[str, str]:
        """Name to value of the cookies a browser would send to ``host`` for ``path``.

        Of two cookies with the same name the one with the longer path wins,
        because a browser sends that one first.
        """
        host = host.lower()
        jar: dict[str, str] = {}
        for cookie in sorted(self.cookies, key=lambda c: len(c.get("path") or "/")):
            domain = self._domain(cookie)
            cookie_path = cookie.get("path") or "/"
            covers = host == domain.lstrip(".") or (
                domain.startswith(".") and host.endswith(domain)
            )
            on_path = path == cookie_path or path.startswith(cookie_path.rstrip("/") + "/")
            if covers and on_path and cookie.get("name") and cookie.get("value"):
                jar[cookie["name"]] = cookie["value"]
        return jar

    def merge_set_cookies(self, url: str, set_cookie_headers: list[str]) -> None:
        """Fold the Set-Cookie headers a response to ``url`` carried into the jar.

        Parsing goes through the stdlib cookie jar (via httpx), so Max-Age
        becomes an absolute expiry, a host-only cookie gets the responding
        host as its domain, and a Domain attribute that does not cover the
        responding host is dropped. A cookie is identified by name, domain
        (leading dot ignored) and path; a known one gets the new value and
        expiry, an unknown one is added. A deletion (empty value or a past
        expiry) is ignored on purpose: a stored credential is only ever
        removed by its owner.
        """
        if not set_cookie_headers:
            return
        response = httpx.Response(
            200,
            headers=[("set-cookie", header) for header in set_cookie_headers],
            request=httpx.Request("GET", url),
        )
        for issued in response.cookies.jar:
            if not issued.value:
                continue
            fresh = {
                "value": issued.value,
                "expires": (
                    datetime.fromtimestamp(issued.expires, UTC).isoformat()
                    if issued.expires
                    else None
                ),
                "secure": issued.secure,
                "httpOnly": any(issued.has_nonstandard_attr(k) for k in ("HttpOnly", "httponly")),
            }
            key = (issued.name, issued.domain.lstrip("."), issued.path)
            known = next(
                (
                    c
                    for c in self.cookies
                    if (c.get("name"), self._domain(c).lstrip("."), c.get("path") or "/") == key
                ),
                None,
            )
            if known is None:
                self.cookies.append(
                    {"name": issued.name, "domain": issued.domain, "path": issued.path, **fresh}
                )
            elif any(known.get(k) != v for k, v in fresh.items()):
                known.update(fresh)
            else:
                continue
            self.issued_hosts.add(urlparse(url).hostname or "")


async def store_connector_cookie_jar(
    *,
    connector_id: uuid.UUID,
    stored: StoredCredentials,
    pool: asyncpg.Pool,
    kek_hex: str,
    jar: CookieJar,
) -> bool:
    """Write the jar back into the stored credentials, the way a browser keeps cookies.

    A rolling session stays alive only if the value the site last issued is
    the one replayed next time. The jar replaces the ``cookies`` of the
    payload of ``stored`` -- the blob the caller read before it made its
    requests -- and the update is guarded on that same blob and the
    connector's org, never on a fresh re-read. A cookie paste in the portal
    after the caller's read therefore always wins and this write is dropped.
    Returns whether the row was written.
    """
    payload = {**stored.payload, "cookies": jar.cookies}
    new_blob = ConnectorCredentialStore(kek_hex).encrypt_credentials_to_blob(
        payload, stored.dek_enc
    )
    status = await pool.execute(
        "UPDATE portal_connectors SET encrypted_credentials = $1"
        " WHERE id = $2 AND encrypted_credentials = $3 AND org_id = $4",
        new_blob,
        connector_id,
        stored.encrypted,
        stored.org_id,
    )
    return status == "UPDATE 1"
