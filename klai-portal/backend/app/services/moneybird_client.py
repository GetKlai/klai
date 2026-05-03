"""
Moneybird REST API client — minimal surface for tenant deprovisioning.

Implements two operations:
  - stop_subscription(subscription_id): pause/stop a recurring invoice.
  - archive_contact(contact_id): archive the Moneybird contact.

Both operations are idempotent: 404 is treated as "already done" (OK).
Other non-2xx responses propagate via raise_for_status().

Base URL is derived from settings.moneybird_admin_id at construction time:
  https://moneybird.com/api/v2/{admin_id}

Token authentication uses settings.moneybird_api_token (Bearer scheme).

If either setting is missing (empty string), the client raises immediately
at construction — this is fail-closed, not fail-open. A misconfigured deploy
fails at startup, not silently on the first deprovisioning call.

Singleton ``moneybird`` is exported at module level (mirrors zitadel.py).

SPEC-INFRA-TENANT-DELETE-001 Phase 6.
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

_MONEYBIRD_API_ROOT = "https://moneybird.com/api/v2"


class MoneybirdClient:
    """Minimal Moneybird REST API client for deprovisioning operations."""

    def __init__(self) -> None:
        admin_id = settings.moneybird_admin_id
        token = settings.moneybird_api_token

        if not admin_id or not admin_id.strip():
            raise ValueError(
                "MONEYBIRD_ADMIN_ID is not configured. Set moneybird_admin_id in settings before using MoneybirdClient."
            )
        if not token or not token.strip():
            raise ValueError(
                "MONEYBIRD_API_TOKEN is not configured. "
                "Set moneybird_api_token in settings before using MoneybirdClient."
            )

        self._base_url = f"{_MONEYBIRD_API_ROOT}/{admin_id}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

    async def stop_subscription(self, subscription_id: str) -> None:
        """Stop a recurring sales invoice (subscription).

        Per Moneybird docs (developer.moneybird.com/api/recurring_sales_invoices):
        DELETE on the resource is the only mechanism to stop automatic invoicing.
        The API behaves polymorphically:
          - If no invoices were ever created from this recurring template, the
            recurring invoice is destroyed → 204.
          - If invoices have been created, the recurring template is deactivated
            (history is preserved) → 204.
        Either way: billing stops. There is no PATCH-based "frequency_type=stopped"
        endpoint — that was a guess in the original implementation that this fix
        replaces.

        Idempotent: 404 means the subscription is already absent or deleted.
        """
        resp = await self._http.delete(f"/recurring_sales_invoices/{subscription_id}")
        if resp.status_code == 404:
            logger.info(
                "moneybird_subscription_already_absent",
                subscription_id=subscription_id,
            )
            return
        resp.raise_for_status()
        logger.info(
            "moneybird_subscription_stopped",
            subscription_id=subscription_id,
            status=resp.status_code,
        )

    async def archive_contact(self, contact_id: str) -> None:
        """Archive a Moneybird contact.

        Uses PATCH /contacts/{contact_id} with archived=true.
        Archived contacts are hidden from the contact list but retained for
        legal/accounting purposes.

        Idempotent: 404 means the contact is already absent.
        """
        resp = await self._http.patch(
            f"/contacts/{contact_id}",
            json={"contact": {"archived": True}},
        )
        if resp.status_code == 404:
            logger.info(
                "moneybird_contact_already_absent",
                contact_id=contact_id,
            )
            return
        resp.raise_for_status()
        logger.info(
            "moneybird_contact_archived",
            contact_id=contact_id,
        )

    async def close(self) -> None:
        """Close the underlying httpx client."""
        await self._http.aclose()


def get_moneybird_client() -> MoneybirdClient:
    """Return a new MoneybirdClient instance.

    Raises ValueError immediately if settings are not configured.
    Callers (deprovisioning orchestrator steps) should call this function
    rather than importing the module-level singleton, so that missing
    configuration fails loudly at the step rather than at import time.

    Pattern: instantiate per-call or cache at orchestrator level.
    """
    return MoneybirdClient()
