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

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.klai_connector_secret}", **get_trace_headers()}

    async def trigger_sync(self, connector_id: str) -> SyncRunData:
        """Trigger an on-demand sync. Returns the created SyncRun (status: running).

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx from klai-connector.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{settings.klai_connector_url}/api/v1/connectors/{connector_id}/sync",
                headers=self._headers(),
            )
            response.raise_for_status()
            return SyncRunData(**response.json())

    async def get_sync_runs(self, connector_id: str, limit: int = 20) -> list[SyncRunData]:
        """Fetch sync history for a connector from klai-connector.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx from klai-connector.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.klai_connector_url}/api/v1/connectors/{connector_id}/syncs",
                params={"limit": min(limit, 100)},
                headers=self._headers(),
            )
            response.raise_for_status()
            return [SyncRunData(**r) for r in response.json()]


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
