from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(path: str = "/internal/mailing/sync-contact", *, token: str | None = None) -> Request:
    headers = []
    if token is not None:
        headers.append((b"authorization", token.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "scheme": "https",
            "server": ("my.getklai.com", 443),
        }
    )


@pytest.mark.asyncio
async def test_mailing_sync_contact_returns_result(monkeypatch):
    from app.api import internal
    from app.services.listmonk import ListmonkSyncResult

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    sync_mock = AsyncMock(return_value=ListmonkSyncResult(subscriber_id=42, lists_added=[3]))
    monkeypatch.setattr("app.services.listmonk.sync_contact", sync_mock)

    body = internal.MailingSyncContactRequest(
        email="Alice@Example.com",
        name="Alice",
        source="twenty_manual",
        audiences=["crm_selected"],
        twentyPersonId="person-1",
    )
    resp = await internal.mailing_sync_contact(_request(), body)

    assert resp.synced is True
    assert resp.subscriber_id == 42
    assert resp.lists_added == [3]
    sync_mock.assert_awaited_once()
    assert sync_mock.call_args.kwargs["email"] == "alice@example.com"
    assert sync_mock.call_args.kwargs["twenty_person_id"] == "person-1"


@pytest.mark.asyncio
async def test_mailing_sync_contact_rejects_invalid_email(monkeypatch):
    from app.api import internal

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    sync_mock = AsyncMock()
    monkeypatch.setattr("app.services.listmonk.sync_contact", sync_mock)

    body = internal.MailingSyncContactRequest(
        email="not-an-email",
        source="website_waitlist",
        audiences=["signups"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await internal.mailing_sync_contact(_request(), body)

    assert exc_info.value.status_code == 400
    sync_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mailing_sync_contact_maps_listmonk_5xx_to_502(monkeypatch):
    from app.api import internal
    from app.services.listmonk import ListmonkAPIError

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr("app.services.listmonk.sync_contact", AsyncMock(side_effect=ListmonkAPIError(500, "boom")))

    body = internal.MailingSyncContactRequest(
        email="alice@example.com",
        source="website_waitlist",
        audiences=["signups"],
    )

    with pytest.raises(HTTPException) as exc_info:
        await internal.mailing_sync_contact(_request(), body)

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_mailing_send_uses_onboarding_template(monkeypatch):
    from app.api import internal
    from app.services.listmonk import ListmonkSendResult

    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    send_mock = AsyncMock(return_value=ListmonkSendResult(sent=True, template_id=5, sent_to="alice@example.com"))
    monkeypatch.setattr("app.services.listmonk.send_onboarding_invite", send_mock)

    body = internal.MailingSendRequest(template="onboarding_invite", email="Alice@Example.com", name="Alice")
    resp = await internal.mailing_send(_request("/internal/mailing/send"), body)

    assert resp.sent is True
    assert resp.template == "onboarding_invite"
    assert resp.template_id == 5
    assert resp.sent_to == "alice@example.com"
    send_mock.assert_awaited_once()
    assert send_mock.call_args.kwargs["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_mailing_endpoint_without_bearer_token_returns_401(monkeypatch):
    from app.api import internal

    monkeypatch.setattr(internal.settings, "internal_secret", "expected-secret")

    with pytest.raises(HTTPException) as exc_info:
        await internal._require_internal_token(_request())

    assert exc_info.value.status_code == 401
