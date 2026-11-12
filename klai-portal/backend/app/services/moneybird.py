import logging

import httpx

from app.core.config import Settings
from app.utils.response_sanitizer import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

logger = logging.getLogger(__name__)


class MoneybirdService:
    def __init__(self, settings: Settings) -> None:
        self._http = httpx.AsyncClient(
            base_url=f"https://moneybird.com/api/v2/{settings.moneybird_admin_id}",
            headers={
                "Authorization": f"Bearer {settings.moneybird_api_token}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

    async def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.is_error:
            sanitized = sanitize_response_body(resp, max_len=200)
            logger.error(
                "Moneybird API %s %s failed: status=%d, body=%s",
                resp.request.method,
                resp.request.url.path,
                resp.status_code,
                sanitized,
            )
            # Raise with the sanitized body too -- the upstream raise message
            # bubbles into structlog at the caller and used to leak the
            # Moneybird Bearer token verbatim when the API echoed it back.
            raise RuntimeError(sanitized)

    async def get_mandate_url(self, contact_id: str) -> str:
        resp = await self._http.post(f"/contacts/{contact_id}/moneybird_payments_mandate/url.json")
        await self._raise_for_status(resp)
        return resp.json()["url"]

    async def cancel_subscription(self, subscription_id: str) -> None:
        resp = await self._http.delete(f"/subscriptions/{subscription_id}.json")
        await self._raise_for_status(resp)

    async def get_invoice_portal_url(self, contact_id: str) -> str:
        resp = await self._http.get(f"/customer_contact_portal/{contact_id}/invoices.json")
        await self._raise_for_status(resp)
        return resp.json()["url"]

    async def close(self) -> None:
        await self._http.aclose()
