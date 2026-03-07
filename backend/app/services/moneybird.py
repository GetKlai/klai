import httpx
from app.core.config import Settings


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
        self._settings = settings

    async def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.is_error:
            raise RuntimeError(resp.text)

    async def create_contact(self, name: str, email: str | None = None) -> dict:
        body: dict = {"contact": {"company_name": name}}
        if email:
            body["contact"]["email"] = email
        resp = await self._http.post("/contacts.json", json=body)
        await self._raise_for_status(resp)
        return resp.json()

    async def get_mandate_url(self, contact_id: str) -> str:
        resp = await self._http.post(
            f"/contacts/{contact_id}/moneybird_payments_mandate/url.json"
        )
        await self._raise_for_status(resp)
        return resp.json()["url"]

    async def create_subscription(self, contact_id: str) -> dict:
        resp = await self._http.post(
            "/subscriptions.json",
            json={
                "subscription": {
                    "contact_id": contact_id,
                    "recurring_sales_invoice_id": self._settings.moneybird_subscription_product_id,
                }
            },
        )
        await self._raise_for_status(resp)
        return resp.json()

    async def cancel_subscription(self, subscription_id: str) -> None:
        resp = await self._http.delete(f"/subscriptions/{subscription_id}.json")
        await self._raise_for_status(resp)

    async def get_invoice_portal_url(self, contact_id: str) -> str:
        resp = await self._http.get(
            f"/customer_contact_portal/{contact_id}/invoices.json"
        )
        await self._raise_for_status(resp)
        return resp.json()["url"]

    async def close(self) -> None:
        await self._http.aclose()
