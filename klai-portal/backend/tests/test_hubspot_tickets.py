"""SPEC-KNOWLEDGE-ESCALATION-001 §4.4 — HubSpot ticket client.

Every request is served by respx on the fixed host ``api.hubapi.com``; the
assertions cover what the client sends (filters, association, auth) and how
it maps HubSpot's answers (401, 403, 5xx) onto ``HubSpotTicketError``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services import hubspot_tickets
from app.services.hubspot_tickets import HubSpotTicketError, HubSpotTickets

API = "https://api.hubapi.com"
KEY = "pat-eu1-00000000-synthetic"


def test_verified_hubspot_constants() -> None:
    """Verified against developers.hubspot.com on 2026-09-29 (see module
    docstring): ticket->contact is HUBSPOT_DEFINED 16 in the tickets guide's
    create example, and a text property holds at most 65,536 characters."""
    assert hubspot_tickets.TICKET_TO_CONTACT_TYPE_ID == 16
    assert hubspot_tickets.TICKET_CONTENT_MAX_CHARS == 65_536
    assert hubspot_tickets.HUBSPOT_API_BASE == API


@pytest.mark.asyncio
@respx.mock
async def test_find_contact_searches_both_emails_and_prefers_the_primary_match() -> None:
    route = respx.post(f"{API}/crm/v3/objects/contacts/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 2,
                "results": [
                    {"id": "11", "properties": {"email": "other@example.com", "firstname": "Old"}},
                    {
                        "id": "22",
                        "properties": {
                            "email": "sam@example.com",
                            "firstname": "Sam",
                            "lastname": "Jansen",
                            "lifecyclestage": "customer",
                        },
                    },
                ],
            },
        )
    )
    async with HubSpotTickets(KEY) as hs:
        contact = await hs.find_contact("Sam@Example.com")

    assert contact is not None
    assert contact.id == "22"
    assert contact.name == "Sam Jansen"
    assert contact.lifecycle_stage == "customer"
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body["filterGroups"] == [
        {"filters": [{"propertyName": "email", "operator": "EQ", "value": "sam@example.com"}]},
        {
            "filters": [
                {"propertyName": "hs_additional_emails", "operator": "CONTAINS_TOKEN", "value": "sam@example.com"}
            ]
        },
    ]
    assert set(body["properties"]) == {"firstname", "lastname", "email", "lifecyclestage"}


@pytest.mark.asyncio
@respx.mock
async def test_create_ticket_sends_pipeline_stage_and_the_contact_association() -> None:
    route = respx.post(f"{API}/crm/v3/objects/tickets").mock(return_value=httpx.Response(201, json={"id": "9001"}))
    async with HubSpotTickets(KEY) as hs:
        ticket_id = await hs.create_ticket(
            subject="Webchat: prijs", content="tekst", pipeline_id="0", stage_id="1", contact_id="22"
        )

    assert ticket_id == "9001"
    body = json.loads(route.calls.last.request.content)
    assert body["properties"] == {
        "subject": "Webchat: prijs",
        "content": "tekst",
        "hs_pipeline": "0",
        "hs_pipeline_stage": "1",
    }
    assert body["associations"] == [
        {"to": {"id": "22"}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 16}]}
    ]


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status_code", "code"), [(401, "invalid_service_key"), (403, "missing_scope"), (500, "hubspot_error")]
)
async def test_errors_map_to_a_typed_exception(status_code: int, code: str) -> None:
    respx.get(f"{API}/crm/v3/pipelines/tickets").mock(return_value=httpx.Response(status_code, json={}))
    async with HubSpotTickets(KEY) as hs:
        with pytest.raises(HubSpotTicketError) as info:
            await hs.ticket_pipelines()

    assert info.value.code == code
    assert KEY not in info.value.reason
    assert str(status_code) in info.value.reason
