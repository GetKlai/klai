"""HTTP client for the klai-connector execution service.

Portal is the control plane (config source of truth).
klai-connector is the execution plane (stateless executor).

Portal calls klai-connector to trigger syncs and retrieve sync history.
klai-connector calls back to portal's internal API to report results.
"""

import logging
from datetime import datetime

import httpx
from pydantic import BaseModel

from app.core.config import settings
from app.trace import get_trace_headers

logger = logging.getLogger(__name__)


class SyncRunData(BaseModel):
    id: str
    connector_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    documents_total: int = 0
    documents_ok: int = 0
    documents_failed: int = 0
    bytes_processed: int = 0
    error_details: list[dict] | None = None


class KlaiConnectorClient:
    """Client for the klai-connector execution service.

    All calls use the shared internal secret for service-to-service auth.
    Raises httpx.HTTPStatusError on 4xx/5xx responses.
    """

    def _headers(self, *, org_id: str | None = None) -> dict[str, str]:
        """Build outbound headers for a klai-connector call.

        SPEC-SEC-TENANT-001 REQ-8.1 / REQ-8.3 (v0.5.0): when ``org_id`` is
        provided (the Zitadel resourceowner string from
        ``PortalOrg.zitadel_org_id``), include it as ``X-Org-ID`` so the
        connector can filter sync routes by tenancy. The portal-caller
        bearer + trace headers continue to authenticate the channel and
        propagate request-id correlation.

        ``org_id=None`` is reserved for callsites that do NOT hit a
        sync-route endpoint (e.g. ``compute_fingerprint``); those continue
        to send no ``X-Org-ID`` because the connector does not require
        tenancy on those paths.
        """
        headers: dict[str, str] = {
            "Authorization": f"Bearer {settings.klai_connector_secret}",
            **get_trace_headers(),
        }
        if org_id is not None:
            headers["X-Org-ID"] = org_id
        return headers

    async def trigger_sync(self, connector_id: str, *, org_id: str) -> SyncRunData:
        """Trigger an on-demand sync. Returns the created SyncRun (status: running).

        Args:
            connector_id: Portal connector UUID.
            org_id: Zitadel resourceowner string (PortalOrg.zitadel_org_id);
                injected as ``X-Org-ID`` on the outbound request so the
                connector can filter the sync-runs query by tenancy
                (SPEC-SEC-TENANT-001 REQ-8.1, keyword-only to make the
                requirement self-documenting at every callsite).

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx from klai-connector.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.klai_connector_url}/api/v1/connectors/{connector_id}/sync",
                headers=self._headers(org_id=org_id),
            )
            response.raise_for_status()
            return SyncRunData(**response.json())

    async def get_sync_runs(
        self,
        connector_id: str,
        *,
        org_id: str,
        limit: int = 20,
    ) -> list[SyncRunData]:
        """Fetch sync history for a connector from klai-connector.

        Args:
            connector_id: Portal connector UUID.
            org_id: Zitadel resourceowner string (see ``trigger_sync``).
            limit: Max rows to return (capped at 100 by the connector).

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx from klai-connector.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.klai_connector_url}/api/v1/connectors/{connector_id}/syncs",
                params={"limit": min(limit, 100)},
                headers=self._headers(org_id=org_id),
            )
            response.raise_for_status()
            return [SyncRunData(**r) for r in response.json()]

    async def delete_sync_runs(self, connector_id: str, *, org_id: str) -> None:
        """Delete every sync_runs row for a connector.

        SPEC-CONNECTOR-CLEANUP-001 REQ-04 (interim) — until the
        ``connector.sync_runs.connector_id`` cross-schema FK with
        ``ON DELETE CASCADE`` to ``public.portal_connectors`` lands,
        the portal cleans the connector's sync history at delete time
        by hitting this endpoint. Without it, every connector deletion
        leaves an audit trail of orphan rows in ``connector.sync_runs``
        keyed on a ``connector_id`` that no longer exists in
        ``portal_connectors``. Discovered live during a Voys end-to-end
        delete-cleanup audit on 2026-04-30.

        Idempotent: zero rows is fine. Always called with the same
        ``X-Org-ID`` as the rest of the connector lifecycle so the
        connector cannot be made to wipe a different tenant's history
        by ID confusion.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{settings.klai_connector_url}/api/v1/connectors/{connector_id}/sync-runs",
                headers=self._headers(org_id=org_id),
            )
            response.raise_for_status()

    async def compute_fingerprint(
        self,
        url: str,
        cookies: list[dict] | None = None,
    ) -> str | None:
        """Compute the canary fingerprint for a URL via klai-connector.

        SPEC-CRAWL-004 REQ-9: called when an admin manually changes the canary
        URL in advanced settings. klai-connector crawls the page with cookie
        injection and returns the 16-char hex SimHash.

        Returns the fingerprint string on success, or None on any failure
        (non-blocking — the connector is saved without canary if this fails).
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{settings.klai_connector_url}/api/v1/compute-fingerprint",
                    headers=self._headers(),
                    json={"url": url, "cookies": cookies},
                )
                response.raise_for_status()
                return response.json().get("fingerprint")
        except Exception:
            logger.warning(
                "compute_fingerprint failed for %s",
                url,
                exc_info=True,
            )
            return None


klai_connector_client = KlaiConnectorClient()
