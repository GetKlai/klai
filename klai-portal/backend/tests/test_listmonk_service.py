from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services import listmonk


@pytest.fixture(autouse=True)
def configure_listmonk(monkeypatch):
    monkeypatch.setattr(listmonk.settings, "listmonk_url", "https://mailing.test")
    monkeypatch.setattr(listmonk.settings, "listmonk_api_user", "api-user")
    monkeypatch.setattr(listmonk.settings, "listmonk_api_token", "api-token")
    monkeypatch.setattr(listmonk.settings, "listmonk_list_crm_selected_id", 3)
    monkeypatch.setattr(listmonk.settings, "listmonk_list_signups_id", 4)
    monkeypatch.setattr(listmonk.settings, "listmonk_list_users_id", 5)
    monkeypatch.setattr(listmonk.settings, "listmonk_list_updates_opt_in_id", 6)
    monkeypatch.setattr(listmonk.settings, "listmonk_tx_onboarding_template_id", 5)


@pytest.mark.asyncio
async def test_upsert_creates_new_subscriber():
    with respx.mock(assert_all_called=True) as router:
        create_route = router.post("https://mailing.test/api/subscribers").mock(
            return_value=httpx.Response(200, json={"data": {"id": 11}})
        )

        result = await listmonk.sync_contact(
            email=" Alice@Example.com ",
            name="Alice",
            source="website_waitlist",
            audiences=["signups", "updates_opt_in"],
            company="Example BV",
            marketing_consent=True,
        )

    assert result.subscriber_id == 11
    assert result.lists_added == [4, 6]
    payload = json.loads(create_route.calls[0].request.content)
    assert payload["email"] == "alice@example.com"
    assert payload["status"] == "enabled"
    assert payload["lists"] == [4, 6]
    assert payload["attribs"]["marketingConsent"] is True


@pytest.mark.asyncio
async def test_duplicate_email_updates_existing_subscriber_and_adds_missing_lists():
    existing = {
        "id": 12,
        "status": "enabled",
        "lists": [{"id": 3, "subscription_status": "confirmed"}],
    }

    with respx.mock(assert_all_called=True) as router:
        router.post("https://mailing.test/api/subscribers").mock(
            return_value=httpx.Response(409, text="duplicate subscriber")
        )
        router.get("https://mailing.test/api/subscribers").mock(
            return_value=httpx.Response(200, json={"data": {"results": [existing]}})
        )
        patch_route = router.patch("https://mailing.test/api/subscribers/12").mock(
            return_value=httpx.Response(200, json={"data": existing})
        )
        add_route = router.put("https://mailing.test/api/subscribers/lists").mock(
            return_value=httpx.Response(200, json={"data": True})
        )

        result = await listmonk.sync_contact(
            email="alice@example.com",
            name="Alice Example",
            source="twenty_manual",
            audiences=["crm_selected", "signups"],
            twenty_person_id="person-1",
        )

    assert result.subscriber_id == 12
    assert result.lists_added == [4]
    patch_payload = json.loads(patch_route.calls[0].request.content)
    assert patch_payload["name"] == "Alice Example"
    assert patch_payload["attribs"]["twentyPersonId"] == "person-1"
    assert "status" not in patch_payload

    add_payload = json.loads(add_route.calls[0].request.content)
    assert add_payload["ids"] == [12]
    assert add_payload["target_list_ids"] == [4]
    assert add_payload["status"] == "confirmed"


@pytest.mark.asyncio
async def test_unsubscribed_subscriber_is_not_reenabled_or_readded_to_lists():
    existing = {
        "id": 13,
        "status": "unsubscribed",
        "lists": [],
    }

    with respx.mock(assert_all_called=False) as router:
        router.post("https://mailing.test/api/subscribers").mock(
            return_value=httpx.Response(409, text="duplicate subscriber")
        )
        router.get("https://mailing.test/api/subscribers").mock(
            return_value=httpx.Response(200, json={"data": {"results": [existing]}})
        )
        patch_route = router.patch("https://mailing.test/api/subscribers/13").mock(
            return_value=httpx.Response(200, json={"data": existing})
        )
        add_route = router.put("https://mailing.test/api/subscribers/lists").mock(
            return_value=httpx.Response(200, json={"data": True})
        )

        result = await listmonk.sync_contact(
            email="alice@example.com",
            name="Alice",
            source="website_waitlist",
            audiences=["signups"],
        )

    assert result.subscriber_id == 13
    assert result.lists_added == []
    patch_payload = json.loads(patch_route.calls[0].request.content)
    assert "status" not in patch_payload
    assert add_route.called is False
