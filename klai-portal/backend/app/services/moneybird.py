import logging
from datetime import date, timedelta

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

    async def create_contact(
        self,
        company_name: str,
        email: str | None = None,
        firstname: str | None = None,
        lastname: str | None = None,
        address: str | None = None,
        zipcode: str | None = None,
        city: str | None = None,
        country: str = "NL",
        tax_number: str | None = None,
        chamber_of_commerce: str | None = None,
        send_invoices_to_email: str | None = None,
    ) -> dict:
        contact: dict = {"company_name": company_name, "country": country}
        if email:
            contact["email"] = email
        if firstname:
            contact["firstname"] = firstname
        if lastname:
            contact["lastname"] = lastname
        if address:
            contact["address1"] = address
        if zipcode:
            contact["zipcode"] = zipcode
        if city:
            contact["city"] = city
        if tax_number:
            contact["tax_number"] = tax_number
        if chamber_of_commerce:
            contact["chamber_of_commerce"] = chamber_of_commerce
        if send_invoices_to_email:
            contact["send_invoices_to_email"] = send_invoices_to_email
        resp = await self._http.post("/contacts.json", json={"contact": contact})
        await self._raise_for_status(resp)
        return resp.json()

    async def get_mandate_url(self, contact_id: str) -> str:
        resp = await self._http.post(f"/contacts/{contact_id}/moneybird_payments_mandate/url.json")
        await self._raise_for_status(resp)
        return resp.json()["url"]

    async def create_subscription(
        self,
        contact_id: str,
        product_id: str,
        frequency_type: str,
        quantity: int = 1,
        reference: str | None = None,
    ) -> dict:
        start_date = (date.today() + timedelta(days=1)).isoformat()
        payload: dict = {
            "contact_id": contact_id,
            "product_id": product_id,
            "frequency_type": frequency_type,
            "frequency": 1,
            "start_date": start_date,
            "quantity": quantity,
        }
        if reference:
            payload["reference"] = reference
        resp = await self._http.post("/subscriptions.json", json={"subscription": payload})
        await self._raise_for_status(resp)
        return resp.json()

    async def cancel_subscription(self, subscription_id: str) -> None:
        resp = await self._http.delete(f"/subscriptions/{subscription_id}.json")
        await self._raise_for_status(resp)

    async def get_invoice_portal_url(self, contact_id: str) -> str:
        resp = await self._http.get(f"/customer_contact_portal/{contact_id}/invoices.json")
        await self._raise_for_status(resp)
        return resp.json()["url"]

    async def close(self) -> None:
        await self._http.aclose()
