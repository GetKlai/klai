from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest

from app.api import webhooks


def _signature(*, method: str, uri: str, body: bytes, timestamp: str, secret: str) -> str:
    source = method.upper().encode("utf-8") + uri.encode("utf-8") + body + timestamp.encode("utf-8")
    return base64.b64encode(hmac.new(secret.encode("utf-8"), source, hashlib.sha256).digest()).decode("ascii")


@pytest.fixture
def hubspot_client(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.database import get_db

    monkeypatch.setattr(webhooks.settings, "hubspot_webchat_client_secret", "test-hubspot-client-secret")
    monkeypatch.setattr(
        webhooks,
        "record_hubspot_agent_reply",
        AsyncMock(return_value={"status": "recorded", "handoff_session_id": 123}),
    )

    app = FastAPI()
    app.include_router(webhooks.router)

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    with TestClient(app, base_url="https://getklai.getklai.com") as client:
        yield client


def test_hubspot_signature_v3_accepts_valid_request() -> None:
    body = b'{"type":"OUTGOING_CHANNEL_MESSAGE_CREATED"}'
    timestamp = "1760000000000"
    uri = "https://getklai.getklai.com/api/webhooks/hubspot/custom-channel"
    signature = _signature(
        method="POST",
        uri=uri,
        body=body,
        timestamp=timestamp,
        secret="secret",
    )

    assert webhooks._verify_hubspot_signature_v3(
        method="POST",
        request_uri=uri,
        request_body=body,
        timestamp=timestamp,
        signature=signature,
        client_secret="secret",
        now_ms=1760000001000,
    )


def test_hubspot_signature_v3_rejects_stale_timestamp() -> None:
    body = b"{}"
    timestamp = "1760000000000"
    uri = "https://getklai.getklai.com/api/webhooks/hubspot/custom-channel"
    signature = _signature(method="POST", uri=uri, body=body, timestamp=timestamp, secret="secret")

    assert not webhooks._verify_hubspot_signature_v3(
        method="POST",
        request_uri=uri,
        request_body=body,
        timestamp=timestamp,
        signature=signature,
        client_secret="secret",
        now_ms=1760000601000,
    )


def test_hubspot_webhook_accepts_signed_outgoing_message(hubspot_client) -> None:
    payload = {
        "type": "OUTGOING_CHANNEL_MESSAGE_CREATED",
        "portalId": "147785398",
        "channelId": "2930388",
        "eventTimestamp": "2026-05-27T12:15:00Z",
        "message": {
            "id": "msg-1",
            "channelAccountId": "3307400689",
            "channelIntegrationThreadIds": ["klai-widget-wgt_abc-test"],
            "text": "Hallo vanaf HubSpot",
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(time.time() * 1000))
    uri = "https://getklai.getklai.com/api/webhooks/hubspot/custom-channel"
    signature = _signature(
        method="POST",
        uri=uri,
        body=body,
        timestamp=timestamp,
        secret="test-hubspot-client-secret",
    )

    response = hubspot_client.post(
        "/api/webhooks/hubspot/custom-channel",
        content=body,
        headers={
            "content-type": "application/json",
            "x-hubspot-request-timestamp": timestamp,
            "x-hubspot-signature-v3": signature,
        },
    )

    assert response.status_code == 204


def test_hubspot_webhook_rejects_unsigned_request(hubspot_client) -> None:
    response = hubspot_client.post(
        "/api/webhooks/hubspot/custom-channel",
        json={"type": "OUTGOING_CHANNEL_MESSAGE_CREATED"},
    )

    assert response.status_code == 401


def test_hubspot_event_log_fields_do_not_include_message_text() -> None:
    fields = webhooks._hubspot_event_log_fields(
        {
            "type": "OUTGOING_CHANNEL_MESSAGE_CREATED",
            "portalId": "147785398",
            "channelId": "2930388",
            "message": {
                "id": "msg-1",
                "channelAccountId": "3307400689",
                "channelIntegrationThreadIds": ["klai-widget-wgt_abc-test"],
                "text": "Niet loggen",
            },
        }
    )

    assert fields["text_length"] == len("Niet loggen")
    assert "text" not in fields
    assert fields["integration_thread_ids"] == ["klai-widget-wgt_abc-test"]
