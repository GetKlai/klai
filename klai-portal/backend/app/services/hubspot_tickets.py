"""HubSpot client for tickets from a reviewed conversation — SPEC-KNOWLEDGE-ESCALATION-001.

Talks to the tenant's own HubSpot account with the tenant's service key
(stored encrypted in ``widget_ticket_settings``). Unlike
``hubspot_custom_channel`` there is no Klai-owned OAuth app here: the key
is the credential, sent as a Bearer token on a fixed host and never put in an
error message.

SPEC §2.5: only the scopes already on the tenant's key may be needed
(conversations.read, crm.objects.contacts.read, crm.objects.tickets.read/write,
crm.schemas.tickets.read/write, sales-email-read). So contacts are searched,
never created, and companies are never read. ``/account-info/v3/details``
is never called either: it accepts only the ``oauth`` scope
(api-reference/account-account-info-v3/details/get-account-info-v3-details),
so the admin enters the HubSpot account id. The scope each call needs is
recorded next to it, verified against developers.hubspot.com on 2026-09-29.

Other contracts verified the same day:
- Association type id 16 (ticket -> contact, HUBSPOT_DEFINED):
  guides/api/crm/associations/associations-v4 and the create example in
  guides/api/crm/objects/tickets.
- A single- or multi-line text property holds at most 65,536 characters
  (knowledge.hubspot.com/properties/property-field-types-in-hubspot); the
  ticket ``content`` property is multi-line text.
- CRM search: several filterGroups are OR'ed; ``CONTAINS_TOKEN`` is a
  documented operator and ``hs_additional_emails`` a default searchable
  contact property (api-reference/search/guide).
- The docs now show dated paths (``/crm/objects/2026-09/...``); the ``/crm/v3``
  paths used here are the ones klai-connector reads for the same accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

HUBSPOT_API_BASE = "https://api.hubapi.com"

TICKET_TO_CONTACT_TYPE_ID = 16
TICKET_CONTENT_MAX_CHARS = 65_536

# A reviewer waits on these calls in the browser; 10 s per request keeps the
# two-call create flow (search + create) under half a minute.
_TIMEOUT_SECONDS = 10.0
_CONTACT_PROPERTIES = ("firstname", "lastname", "email", "lifecyclestage")


class HubSpotTicketError(Exception):
    """A HubSpot call failed. ``reason`` is short, readable, and key-free.

    ``code`` is ``invalid_service_key`` (401), ``missing_scope`` (403) or
    ``hubspot_error`` for everything else, so the admin routes can answer
    with the contract's 422 details.
    """

    def __init__(self, reason: str, *, code: str = "hubspot_error") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class Contact:
    id: str
    name: str | None
    lifecycle_stage: str | None


def _error_for(method: str, path: str, status_code: int) -> HubSpotTicketError:
    what = f"HubSpot {method} {path} returned {status_code}"
    if status_code == 401:
        return HubSpotTicketError(f"{what}: the service key is invalid", code="invalid_service_key")
    if status_code == 403:
        return HubSpotTicketError(f"{what}: the service key lacks a required scope", code="missing_scope")
    return HubSpotTicketError(what)


def _results(payload: dict[str, Any], path: str) -> list[dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, list):
        raise HubSpotTicketError(f"HubSpot {path} answered without a results list")
    return results


class HubSpotTickets:
    """One HubSpot session for one service key: ``async with HubSpotTickets(key) as hs``."""

    def __init__(self, service_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=HUBSPOT_API_BASE,
            timeout=_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {service_key}"},
        )

    async def __aenter__(self) -> HubSpotTickets:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self._client.aclose()

    async def _json(self, method: str, path: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise HubSpotTicketError(f"HubSpot {method} {path} did not answer ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise _error_for(method, path, response.status_code)
        return response.json()

    async def ticket_pipelines(self) -> list[dict[str, Any]]:
        """``[{id, label, stages: [{id, label}]}]`` — the admin route's response shape."""
        # Scope: any one of 94, including `crm.objects.contacts.read`
        # (https://developers.hubspot.com/docs/api-reference/crm-pipelines-v3/guide).
        # This is also the admin route's key check: 401/403 surface here.
        path = "/crm/v3/pipelines/tickets"
        return [
            {
                "id": str(pipeline["id"]),
                "label": str(pipeline.get("label") or ""),
                "stages": [
                    {"id": str(stage["id"]), "label": str(stage.get("label") or "")}
                    for stage in pipeline.get("stages") or []
                ],
            }
            for pipeline in _results(await self._json("GET", path), path)
        ]

    async def find_contact(self, email: str) -> Contact | None:
        """Primary email or an additional email; the primary-email match wins."""
        # Scope: `crm.objects.contacts.read` (or .write)
        # (https://developers.hubspot.com/docs/api-reference/crm-contacts-v3/guide).
        email = email.strip().lower()
        path = "/crm/v3/objects/contacts/search"
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]},
                {"filters": [{"propertyName": "hs_additional_emails", "operator": "CONTAINS_TOKEN", "value": email}]},
            ],
            "properties": list(_CONTACT_PROPERTIES),
            "limit": 10,
        }
        results = _results(await self._json("POST", path, json=body), path)
        if not results:
            return None
        primary = next(
            (r for r in results if str((r.get("properties") or {}).get("email") or "").lower() == email), results[0]
        )
        props = primary.get("properties") or {}
        name = " ".join(part for part in (props.get("firstname"), props.get("lastname")) if part) or None
        return Contact(id=str(primary["id"]), name=name, lifecycle_stage=props.get("lifecyclestage") or None)

    async def create_ticket(
        self, *, subject: str, content: str, pipeline_id: str, stage_id: str, contact_id: str | None
    ) -> str:
        # Scope: `crm.objects.tickets.write`; the reference names no extra
        # scope for the association to an existing contact
        # (https://developers.hubspot.com/docs/api-reference/crm-tickets-v3/guide).
        body: dict[str, Any] = {
            "properties": {
                "subject": subject,
                "content": content,
                "hs_pipeline": pipeline_id,
                "hs_pipeline_stage": stage_id,
            },
        }
        if contact_id is not None:
            body["associations"] = [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": TICKET_TO_CONTACT_TYPE_ID}
                    ],
                }
            ]
        data = await self._json("POST", "/crm/v3/objects/tickets", json=body)
        return str(data["id"])
