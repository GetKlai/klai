"""HTTP client for the portal control plane.

klai-connector is stateless — it fetches connector config from portal at sync time
and reports results back via the sync-status callback.

This decoupling means:
- Portal is the single source of truth for connector configuration.
- klai-connector never stores secrets or KB routing config locally.
- Config changes in portal take effect on the next sync run automatically.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PortalConnectorConfig:
    """Connector configuration fetched from the portal control plane."""

    connector_id: str
    kb_id: int
    kb_slug: str
    zitadel_org_id: str  # Used for Qdrant collection partitioning
    connector_type: str
    config: dict[str, Any]
    schedule: str | None
    is_enabled: bool
    allowed_assertion_modes: list[str] | None = None


class PortalClient:
    """Calls portal's internal API for config and status callbacks.

    Args:
        settings: Application settings (reads portal_api_url + portal_internal_secret).
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.portal_api_url
        self._secret = settings.portal_internal_secret

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"}

    async def get_connector_config(self, connector_id: uuid.UUID) -> PortalConnectorConfig:
        """Fetch connector configuration from portal.

        Args:
            connector_id: Portal connector UUID (portal_connectors.id).

        Returns:
            PortalConnectorConfig with all fields needed to run the sync.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx (404 = connector deleted in portal).
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/internal/connectors/{connector_id}",
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            return PortalConnectorConfig(
                connector_id=data["connector_id"],
                kb_id=data["kb_id"],
                kb_slug=data["kb_slug"],
                zitadel_org_id=data["zitadel_org_id"],
                connector_type=data["connector_type"],
                config=data["config"],
                schedule=data.get("schedule"),
                is_enabled=data["is_enabled"],
                allowed_assertion_modes=data.get("allowed_assertion_modes"),
            )

    async def report_sync_status(
        self,
        connector_id: uuid.UUID,
        sync_run_id: uuid.UUID,
        sync_status: str,
        completed_at: datetime,
        documents_total: int,
        documents_ok: int,
        documents_failed: int,
        bytes_processed: int,
        error_details: list[dict[str, Any]] | None,
    ) -> None:
        """Report sync run results to portal.

        Best-effort: logs and swallows errors so sync runs don't fail due to
        callback issues. Portal's connector record gets updated asynchronously.

        Args:
            connector_id: Portal connector UUID.
            sync_run_id: klai-connector's sync run UUID (for tracing).
            sync_status: Final status string ('completed', 'failed', 'auth_error').
            completed_at: Timestamp of sync completion.
            documents_total: Total documents seen.
            documents_ok: Documents successfully ingested.
            documents_failed: Documents that failed.
            bytes_processed: Total bytes processed.
            error_details: Per-document error list, or None.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self._base_url}/internal/connectors/{connector_id}/sync-status",
                    headers=self._headers(),
                    json={
                        "sync_run_id": str(sync_run_id),
                        "status": sync_status,
                        "completed_at": completed_at.isoformat(),
                        "documents_total": documents_total,
                        "documents_ok": documents_ok,
                        "documents_failed": documents_failed,
                        "bytes_processed": bytes_processed,
                        "error_details": error_details,
                    },
                )
                response.raise_for_status()
        except Exception:
            logger.exception(
                "Failed to report sync status to portal for connector %s (sync run %s)",
                connector_id,
                sync_run_id,
            )

    async def report_quality_event(
        self,
        *,
        connector_id: uuid.UUID,
        sync_run_id: uuid.UUID,
        org_id: str,
        quality_status: str,
        reason: str,
        metric: float | int | None = None,
    ) -> None:
        """Emit a knowledge.sync_quality_degraded product event via portal.

        # @MX:NOTE: [AUTO] SPEC-CRAWL-003 REQ-15 — product event on quality transitions.
        # Best-effort: logs and swallows errors so sync failures don't block on event emission.

        Args:
            connector_id: Portal connector UUID.
            sync_run_id: The sync run UUID for correlation.
            org_id: Zitadel org ID (used as event org context).
            quality_status: 'degraded' or 'failed'.
            reason: 'canary_mismatch', 'boilerplate_cluster', or 'auth_walled_pages'.
            metric: Similarity score (canary), cluster ratio (Layer C), or count (Layer B).
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self._base_url}/internal/product-events",
                    headers=self._headers(),
                    json={
                        "event_type": "knowledge.sync_quality_degraded",
                        "org_id": org_id,
                        "connector_id": str(connector_id),
                        "sync_run_id": str(sync_run_id),
                        "quality_status": quality_status,
                        "reason": reason,
                        "metric": metric,
                    },
                )
                response.raise_for_status()
        except Exception:
            logger.warning(
                "Failed to emit quality event to portal for connector %s (quality_status=%s, reason=%s)",
                connector_id,
                quality_status,
                reason,
            )

    async def update_credentials(
        self,
        connector_id: str,
        access_token: str,
        token_expiry: str | None = None,
    ) -> None:
        """Write back refreshed access token to portal.

        Called by OAuth adapters after refreshing an access token. Portal
        re-encrypts the payload via ConnectorCredentialStore (SPEC-KB-020).

        Best-effort: logs and swallows errors so sync runs don't fail due
        to callback issues. The next run will refresh again if needed.

        Args:
            connector_id: Portal connector UUID (string form).
            access_token: Fresh OAuth access token (NEVER logged).
            token_expiry: ISO-8601 UTC timestamp of token expiry (optional).
        """
        # @MX:NOTE: [AUTO] Never log access_token value — only metadata is logged.
        try:
            payload: dict[str, Any] = {"access_token": access_token}
            if token_expiry is not None:
                payload["token_expiry"] = token_expiry
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.patch(
                    f"{self._base_url}/internal/connectors/{connector_id}/credentials",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
        except Exception:
            logger.exception(  # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
                "Failed to write back refreshed credentials to portal for connector %s (has_expiry=%s)",
                connector_id,
                token_expiry is not None,
            )
