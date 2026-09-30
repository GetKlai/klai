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
    """Verified against developers.hubspot.com (see module docstring): the
    tickets guide's create example associates a contact with HUBSPOT_DEFINED
    16 and a company with 26 (ticket to primary company); the associations
    table lists 1 as contact to primary company; a text property holds at
    most 65,536 characters."""
    assert hubspot_tickets.TICKET_TO_CONTACT_TYPE_ID == 16
    assert hubspot_tickets.TICKET_TO_COMPANY_TYPE_ID == 26
    assert hubspot_tickets.CONTACT_TO_PRIMARY_COMPANY_TYPE_ID == 1
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
    assert set(body["properties"]) == {"firstname", "lastname", "email", "lifecyclestage", "hs_additional_emails"}


@pytest.mark.asyncio
@respx.mock
async def test_find_contact_attaches_only_a_contact_that_really_carries_the_email() -> None:
    """A search hit whose primary and additional emails both differ from the
    visitor's must not become the ticket's contact: that would file one
    visitor's transcript under another customer."""
    respx.post(f"{API}/crm/v3/objects/contacts/search").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "31",
                            "properties": {"email": "sam.jansen@example.com", "hs_additional_emails": "sj@example.com"},
                        }
                    ]
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "32",
                            "properties": {
                                "email": "office@example.com",
                                "hs_additional_emails": "noa@example.com;sam@example.com",
                            },
                        }
                    ]
                },
            ),
        ]
    )
    async with HubSpotTickets(KEY) as hs:
        unrelated = await hs.find_contact("sam@example.com")
        secondary = await hs.find_contact("sam@example.com")

    assert unrelated is None
    assert secondary is not None
    assert secondary.id == "32"


@pytest.mark.asyncio
@respx.mock
async def test_create_ticket_sends_pipeline_stage_and_the_contact_and_company_associations() -> None:
    route = respx.post(f"{API}/crm/v3/objects/tickets").mock(return_value=httpx.Response(201, json={"id": "9001"}))
    async with HubSpotTickets(KEY) as hs:
        ticket_id = await hs.create_ticket(
            subject="Webchat: prijs",
            content="tekst",
            pipeline_id="0",
            stage_id="1",
            contact_id="22",
            company_id="77",
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
        {"to": {"id": "22"}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 16}]},
        {"to": {"id": "77"}, "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 26}]},
    ]


@pytest.mark.asyncio
@respx.mock
async def test_create_contact_sends_email_and_the_name_split_at_the_first_space() -> None:
    route = respx.post(f"{API}/crm/v3/objects/contacts").mock(return_value=httpx.Response(201, json={"id": "41"}))
    async with HubSpotTickets(KEY) as hs:
        created = await hs.create_contact("Sam@Example.com", "Sam van Dijk")

    assert created == ("41", True)
    assert json.loads(route.calls.last.request.content) == {
        "properties": {"email": "sam@example.com", "firstname": "Sam", "lastname": "van Dijk"}
    }


@pytest.mark.asyncio
@respx.mock
async def test_create_contact_409_returns_the_existing_id_from_the_error() -> None:
    """HubSpot answers a duplicate email with 409 and the id only in the
    message text (shape pinned in CONTACT_EXISTS_ID_PATTERN's comment)."""
    respx.post(f"{API}/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(
            409,
            json={
                "status": "error",
                "message": "Contact already exists. Existing ID: 216799",
                "correlationId": "00000000-0000-0000-0000-000000000000",
                "category": "CONFLICT",
            },
        )
    )
    async with HubSpotTickets(KEY) as hs:
        created = await hs.create_contact("sam@example.com", None)

    assert created == ("216799", False)


@pytest.mark.asyncio
@respx.mock
async def test_create_contact_409_without_an_id_is_an_error_not_a_guess() -> None:
    respx.post(f"{API}/crm/v3/objects/contacts").mock(
        return_value=httpx.Response(409, json={"status": "error", "message": "Conflict", "category": "CONFLICT"})
    )
    async with HubSpotTickets(KEY) as hs:
        with pytest.raises(HubSpotTicketError) as info:
            await hs.create_contact("sam@example.com", "Sam")

    assert "409" in info.value.reason


@pytest.mark.asyncio
@respx.mock
async def test_primary_company_prefers_the_primary_label_over_the_first_result() -> None:
    route = respx.get(f"{API}/crm/v4/objects/contacts/22/associations/companies").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"toObjectId": 70, "associationTypes": [{"category": "HUBSPOT_DEFINED", "typeId": 279}]},
                    {
                        "toObjectId": 77,
                        "associationTypes": [
                            {"category": "HUBSPOT_DEFINED", "typeId": 1, "label": "Primary"},
                            {"category": "HUBSPOT_DEFINED", "typeId": 279},
                        ],
                    },
                ]
            },
        )
    )
    async with HubSpotTickets(KEY) as hs:
        company_id = await hs.primary_company_id("22")

    assert company_id == "77"
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_account_details_reads_portal_id_and_ui_domain() -> None:
    respx.get(f"{API}/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={"portalId": 4455, "uiDomain": "app-eu1.hubspot.com"})
    )
    async with HubSpotTickets(KEY) as hs:
        account = await hs.account_details()

    assert (account.portal_id, account.ui_domain) == (4455, "app-eu1.hubspot.com")


@pytest.mark.asyncio
@respx.mock
async def test_account_details_refuses_a_ui_domain_outside_hubspot() -> None:
    """The ui domain becomes the host of every ticket link; a value that is
    not a hubspot.com host must not reach an href."""
    respx.get(f"{API}/account-info/v3/details").mock(
        return_value=httpx.Response(200, json={"portalId": 4455, "uiDomain": "evil.example.com"})
    )
    async with HubSpotTickets(KEY) as hs:
        with pytest.raises(HubSpotTicketError):
            await hs.account_details()


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
