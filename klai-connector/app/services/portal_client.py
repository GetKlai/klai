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
    # owner_user_id: Zitadel user_id of the connector creator. Forwarded
    # to knowledge-ingest /ingest/v1/document as ``req.user_id``.
    # Required by the personal-KB owner-binding check; absent value causes
    # 403 personal_kb_owner_mismatch on syncs to ``personal-{user}`` KBs.
    owner_user_id: str | None = None

    @property
    def id(self) -> str:
        """Alias for connector_id. Adapter code (ms_docs, google_drive) reads
        connector.id; test fixtures historically use SimpleNamespace(id=...).
        Keeping the dataclass field name connector_id while exposing .id keeps
        both call sites correct without refactoring all adapters.
        """
        return self.connector_id


@dataclass
class ScheduledConnector:
    """One entry of the portal's scheduled-connectors feed (scheduler input)."""

    connector_id: uuid.UUID
    org_id: str
    schedule: str
    connector_type: str
    has_saved_credentials: bool


class PortalClient:
    """Calls portal's internal API for config and status callbacks.

    Args:
        settings: Application settings (reads portal_api_url + portal_internal_secret).
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.portal_api_url
        self._secret = settings.portal_internal_secret

    def _headers(self) -> dict[str, str]:
        # SPEC-SEC-INTERNAL-001 REQ-9.3 / AC-9.4: never emit ``Bearer ``
        # (literal trailing space) on the wire. The startup validator on
        # Settings enforces non-empty -- this guard is the second layer that
        # also catches Settings.model_construct() bypass (used in some test
        # fixtures) so the contract holds even when validation is skipped.
        if not self._secret:
            raise RuntimeError(
                "PortalClient cannot send an empty Bearer secret -- portal_internal_secret "
                "is empty (SPEC-SEC-INTERNAL-001 REQ-9.3)."
            )
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
                owner_user_id=data.get("owner_user_id"),
            )

    async def list_scheduled_connectors(self) -> list[ScheduledConnector]:
        """Fetch all enabled portal connectors that carry a cron schedule.

        Feeds ConnectorScheduler: one entry per connector the portal wants
        synced on a schedule, with the tenant org_id the sync run needs.

        Returns:
            List of ScheduledConnector (connector_id, org_id, crontab schedule,
            connector_type, has_saved_credentials).

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self._base_url}/internal/scheduled-connectors",
                headers=self._headers(),
            )
            response.raise_for_status()
            return [
                ScheduledConnector(
                    connector_id=uuid.UUID(item["connector_id"]),
                    org_id=item["zitadel_org_id"],
                    schedule=item["schedule"],
                    connector_type=item["connector_type"],
                    has_saved_credentials=item["has_saved_credentials"],
                )
                for item in response.json()
            ]

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
        documents_changed: int | None,
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
            documents_changed: Documents whose knowledge actually changed in
                this run (real ingests plus deletions), or None when the path
                cannot know. The portal reanalyses support cases only for a
                positive count, so an unchanged sync costs no LLM calls.
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
                        "documents_changed": documents_changed,
                    },
                )
                response.raise_for_status()
        except Exception:
            logger.exception(
                "Failed to report sync status to portal for connector %s (sync run %s)",
                connector_id,
                sync_run_id,
            )

    async def send_support_case(
        self,
        connector_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Post one HubSpot support case to the portal evidence endpoint.

        SPEC-RAG-SUPPORT-GAP: the portal derives org/owner/KB from the
        active connector; the payload never selects another tenant. Unlike
        the best-effort status callback this is NOT swallowed — the caller
        must know whether the evidence was durably stored before it counts
        the case as synced or reconciles deletions.

        Args:
            connector_id: Portal connector UUID.
            payload: A validated ``CasePayload`` (see support_source).

        Returns:
            ``{case_id, status, changed, findings_count}`` from portal.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx. The caller fails the run and
                must not reconcile from a partial snapshot.
        """
        # The portal persists evidence, then analyzes synchronously before
        # responding (bounded extraction + retrieval + judge), so this
        # timeout must cover the whole request, not just the write.
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/connectors/{connector_id}/support-cases",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def reconcile_support_cases(
        self,
        connector_id: uuid.UUID,
        external_ids: list[str],
    ) -> None:
        """Delete portal cases no longer in this connector's selected scope.

        SPEC-RAG-SUPPORT-GAP: only ever called after a fully successful
        snapshot with every case write durable. A partial or failed run
        must never reach here (see SyncEngine._run_hubspot_support_sync).

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/connectors/{connector_id}/support-cases/reconcile",
                headers=self._headers(),
                json={"external_ids": external_ids},
            )
            response.raise_for_status()

    async def update_credentials(
        self,
        connector_id: str,
        access_token: str,
        token_expiry: str | None = None,
        refresh_token: str | None = None,
    ) -> None:
        """Write back refreshed access token (+ optionally rotated refresh token) to portal.

        Called by OAuth adapters after refreshing an access token. Portal
        re-encrypts the payload via ConnectorCredentialStore (SPEC-KB-020).

        ``refresh_token`` is only sent when the provider rotated it
        (Microsoft rotates on every refresh, SPEC-KB-MS-DOCS-001 R9). For
        providers that do not rotate (Google Drive typical flow), callers
        pass ``None`` and the stored refresh_token is left untouched.

        Best-effort: logs and swallows errors so sync runs don't fail due
        to callback issues. The next run will refresh again if needed.

        Args:
            connector_id: Portal connector UUID (string form).
            access_token: Fresh OAuth access token (NEVER logged).
            token_expiry: ISO-8601 UTC timestamp of token expiry (optional).
            refresh_token: Rotated refresh token (optional; NEVER logged).
        """
        # @MX:NOTE: [AUTO] Never log access_token or refresh_token values.
        try:
            payload: dict[str, Any] = {"access_token": access_token}
            if token_expiry is not None:
                payload["token_expiry"] = token_expiry
            if refresh_token is not None:
                payload["refresh_token"] = refresh_token
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.patch(
                    f"{self._base_url}/internal/connectors/{connector_id}/credentials",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
        except Exception:
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            logger.exception(
                "Failed to write back refreshed credentials to portal "
                "for connector %s (has_expiry=%s, rotated_refresh=%s)",
                connector_id,
                token_expiry is not None,
                refresh_token is not None,
            )
