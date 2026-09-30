"""HubSpot client for tickets from a reviewed conversation — SPEC-KNOWLEDGE-ESCALATION-001.

Talks to the tenant's own HubSpot account with the tenant's service key
(stored encrypted in ``widget_ticket_settings``). Unlike
``hubspot_custom_channel`` there is no Klai-owned OAuth app here: the key
is the credential, sent as a Bearer token on a fixed host and never put in an
error message.

SPEC §2.5 (v0.4.0): the full flow needs crm.objects.tickets.write,
crm.objects.contacts.read, crm.objects.contacts.write,
crm.objects.companies.read and, where a service key can get it, ``oauth``.
The tenant key today lacks the last three, so every call that needs one of
them can answer 403; the callers turn that 403 into a per-ticket status
instead of failing the ticket. The scope each call needs is recorded next to
it, verified against developers.hubspot.com on 2026-09-29 and 2026-09-30.

Other contracts verified:
- Association type ids (HUBSPOT_DEFINED): 16 ticket -> contact and 26
  ticket -> primary company in the create example of the tickets guide
  (docs/api-reference/latest/crm/objects/tickets/guide); the associations
  table (docs/api-reference/latest/crm/associations/associate-records/guide)
  lists 16, 26 (and 339 for an unlabeled ticket -> company) plus 1 contact ->
  primary company, 279 unlabeled contact -> company.
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

import re
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx

HUBSPOT_API_BASE = "https://api.hubapi.com"

TICKET_TO_CONTACT_TYPE_ID = 16
# Primary, as in the tickets guide's own create example: the company then
# shows as the ticket's company in HubSpot, not only as one association.
TICKET_TO_COMPANY_TYPE_ID = 26
CONTACT_TO_PRIMARY_COMPANY_TYPE_ID = 1
TICKET_CONTENT_MAX_CHARS = 65_536

# POST /crm/v3/objects/contacts answers a duplicate email with 409 and the id
# only inside the message: {"status": "error", "message": "Contact already
# exists. Existing ID: 216799192486", "correlationId": "...", "category":
# "CONFLICT"}. The reference pages do not document this body; the shape is the
# one reported on HubSpot's own SDK tracker
# (https://github.com/HubSpot/hubspot-api-nodejs/issues/545) and in the
# community threads that link it. There is no CONTACT_EXISTS code in the v3
# body (that name belonged to the v1 contacts API), so the message is parsed.
CONTACT_EXISTS_ID_PATTERN = re.compile(r"Existing ID: (\d+)")

# The ui domain from account-info becomes the host of every ticket link the
# portal renders, so only a hubspot.com host is accepted (e.g.
# app.hubspot.com, app-eu1.hubspot.com).
_UI_DOMAIN_PATTERN = re.compile(r"[a-z0-9-]+\.hubspot\.com")

# A reviewer waits on these calls in the browser. The create flow is at most
# five calls (search, contact create answering 409, company lookup, ticket,
# ticket retry without the company), so 10 s each stays under a minute.
_TIMEOUT_SECONDS = 10.0
_CONTACT_PROPERTIES = ("firstname", "lastname", "email", "lifecyclestage", "hs_additional_emails")


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


@dataclass(frozen=True)
class Account:
    portal_id: int
    ui_domain: str


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

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        try:
            return await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            raise HubSpotTicketError(f"HubSpot {method} {path} did not answer ({type(exc).__name__})") from exc

    async def _json(
        self, method: str, path: str, *, json: dict[str, Any] | None = None, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = await self._request(method, path, json=json, params=params)
        if response.status_code >= 400:
            raise _error_for(method, path, response.status_code)
        return response.json()

    async def account_details(self) -> Account:
        """The account the key belongs to and the host its records open on."""
        # Scope: `oauth` per the reference page
        # (https://developers.hubspot.com/docs/api-reference/account-account-info-v3/details/get-account-info-v3-details);
        # the OpenAPI catalogue lists `portalId` and `uiDomain` as required.
        # A key without it answers 403, which the admin route turns into a
        # manually entered account id.
        path = "/account-info/v3/details"
        data = await self._json("GET", path)
        portal_id, ui_domain = data.get("portalId"), data.get("uiDomain")
        if (
            not isinstance(portal_id, int)
            or not isinstance(ui_domain, str)
            or not _UI_DOMAIN_PATTERN.fullmatch(ui_domain)
        ):
            raise HubSpotTicketError(f"HubSpot {path} answered without a usable portalId and uiDomain")
        return Account(portal_id=portal_id, ui_domain=ui_domain)

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

        def emails(result: dict[str, Any]) -> tuple[str, set[str]]:
            props = result.get("properties") or {}
            # HubSpot stores additional emails as one semicolon-separated string.
            extra = str(props.get("hs_additional_emails") or "").lower().split(";")
            return str(props.get("email") or "").lower(), {e.strip() for e in extra}

        # CONTAINS_TOKEN tokenises, so a hit is not proof the contact carries this
        # exact address; an unverified hit would file the transcript under
        # another customer, so it counts as not found.
        primary = next((r for r in results if emails(r)[0] == email), None) or next(
            (r for r in results if email in emails(r)[1]), None
        )
        if primary is None:
            return None
        props = primary.get("properties") or {}
        name = " ".join(part for part in (props.get("firstname"), props.get("lastname")) if part) or None
        return Contact(id=str(primary["id"]), name=name, lifecycle_stage=props.get("lifecyclestage") or None)

    async def create_contact(self, email: str, name: str | None) -> tuple[str, bool]:
        """``(contact_id, created)``; ``created`` is False when HubSpot already
        had the email (409), which happens when the search index lags."""
        # Scope: `crm.objects.contacts.write`
        # (https://developers.hubspot.com/docs/api-reference/latest/crm/objects/contacts/guide).
        path = "/crm/v3/objects/contacts"
        properties = {"email": email.strip().lower()}
        # SPEC §2.6: the widget asks one name field; split at the first space.
        first, _, last = (name or "").strip().partition(" ")
        if first:
            properties["firstname"] = first
        if last.strip():
            properties["lastname"] = last.strip()
        response = await self._request("POST", path, json={"properties": properties})
        if response.status_code == 409:
            match = CONTACT_EXISTS_ID_PATTERN.search(str(response.json().get("message") or ""))
            if match is None:
                raise HubSpotTicketError(f"HubSpot POST {path} returned 409 without an existing contact id")
            return match.group(1), False
        if response.status_code >= 400:
            raise _error_for("POST", path, response.status_code)
        return str(response.json()["id"]), True

    async def primary_company_id(self, contact_id: str) -> str | None:
        """The contact's primary company, else its first associated company."""
        # Scope: any one of 79, including `crm.objects.contacts.read` and
        # `crm.objects.companies.read` (Associations v4 OpenAPI,
        # GET /crm/v4/objects/{objectType}/{objectId}/associations/{toObjectType};
        # response `results[].toObjectId` + `results[].associationTypes[].typeId`).
        # One page of up to 500 is plenty: only the first company is used.
        path = f"/crm/v4/objects/contacts/{contact_id}/associations/companies"
        results = _results(await self._json("GET", path), path)
        primary = next(
            (
                r
                for r in results
                if any(
                    t.get("category") == "HUBSPOT_DEFINED" and t.get("typeId") == CONTACT_TO_PRIMARY_COMPANY_TYPE_ID
                    for t in r.get("associationTypes") or []
                )
            ),
            None,
        )
        chosen = primary or (results[0] if results else None)
        return str(chosen["toObjectId"]) if chosen is not None else None

    async def company_name(self, company_id: str) -> str | None:
        # Scope: `crm.objects.companies.read`
        # (https://developers.hubspot.com/docs/api-reference/latest/crm/objects/companies/guide).
        path = f"/crm/v3/objects/companies/{company_id}"
        data = await self._json("GET", path, params={"properties": "name"})
        return (data.get("properties") or {}).get("name") or None

    async def create_ticket(
        self,
        *,
        subject: str,
        content: str,
        pipeline_id: str,
        stage_id: str,
        contact_id: str | None,
        company_id: str | None,
    ) -> str:
        # Scope: `crm.objects.tickets.write`
        # (https://developers.hubspot.com/docs/api-reference/latest/crm/objects/tickets/guide).
        # The reference names no extra scope for the associations, but a key
        # that cannot read companies may still be refused the company one,
        # which is why the caller retries without it on a 403.
        body: dict[str, Any] = {
            "properties": {
                "subject": subject,
                "content": content,
                "hs_pipeline": pipeline_id,
                "hs_pipeline_stage": stage_id,
            },
        }
        associations = [
            {"to": {"id": to_id}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}]}
            for to_id, type_id in ((contact_id, TICKET_TO_CONTACT_TYPE_ID), (company_id, TICKET_TO_COMPANY_TYPE_ID))
            if to_id is not None
        ]
        if associations:
            body["associations"] = associations
        data = await self._json("POST", "/crm/v3/objects/tickets", json=body)
        return str(data["id"])
