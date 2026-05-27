"""HubSpot Custom Channel client for Klai webchat support fallback.

The HubSpot app/channel are owned by Klai. Per-widget state stores only
HubSpot identifiers in ``widget_config``; OAuth secrets stay in server config.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from app.core.config import settings


class HubSpotNotConfiguredError(RuntimeError):
    """Raised when the server lacks HubSpot OAuth configuration."""


class HubSpotAPIError(RuntimeError):
    """Raised when HubSpot returns an unsuccessful API response."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class HubSpotChannelAccount:
    id: str
    channel_id: str
    inbox_id: str
    name: str
    active: bool
    authorized: bool
    archived: bool


def hubspot_webchat_configured() -> bool:
    return bool(
        settings.hubspot_webchat_client_id
        and settings.hubspot_webchat_client_secret
        and settings.hubspot_webchat_refresh_token
        and settings.hubspot_webchat_custom_channel_id
        and settings.hubspot_webchat_inbox_id
    )


def _require_configured() -> None:
    if not hubspot_webchat_configured():
        raise HubSpotNotConfiguredError("HubSpot webchat support is not configured")


def _account_from_payload(payload: dict[str, Any]) -> HubSpotChannelAccount:
    return HubSpotChannelAccount(
        id=str(payload["id"]),
        channel_id=str(payload["channelId"]),
        inbox_id=str(payload["inboxId"]),
        name=str(payload.get("name") or ""),
        active=bool(payload.get("active")),
        authorized=bool(payload.get("authorized")),
        archived=bool(payload.get("archived")),
    )


async def _access_token() -> str:
    _require_configured()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://api.hubapi.com/oauth/v1/token",
            data={
                "grant_type": "refresh_token",
                "client_id": settings.hubspot_webchat_client_id,
                "client_secret": settings.hubspot_webchat_client_secret,
                "refresh_token": settings.hubspot_webchat_refresh_token,
            },
        )
    if response.status_code >= 400:
        raise HubSpotAPIError(response.status_code, "HubSpot OAuth refresh failed")
    token = response.json().get("access_token")
    if not token:
        raise HubSpotAPIError(502, "HubSpot OAuth refresh returned no access token")
    return str(token)


async def _request(method: str, path: str, *, json_body: dict[str, Any] | None = None) -> dict[str, Any] | None:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.request(
            method,
            f"https://api.hubapi.com{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=json_body,
        )
    if response.status_code >= 400:
        raise HubSpotAPIError(response.status_code, "HubSpot API request failed")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


async def list_channel_accounts() -> list[HubSpotChannelAccount]:
    channel_id = settings.hubspot_webchat_custom_channel_id
    payload = await _request("GET", f"/conversations/custom-channels/2026-03/{channel_id}/channel-accounts?limit=100")
    return [_account_from_payload(item) for item in (payload or {}).get("results", [])]


async def create_channel_account() -> HubSpotChannelAccount:
    channel_id = settings.hubspot_webchat_custom_channel_id
    payload = await _request(
        "POST",
        f"/conversations/custom-channels/2026-03/{channel_id}/channel-accounts",
        json_body={
            "authorized": True,
            "inboxId": settings.hubspot_webchat_inbox_id,
            "name": settings.hubspot_webchat_channel_account_name,
            "deliveryIdentifier": {
                "type": "CHANNEL_SPECIFIC_OPAQUE_ID",
                "value": settings.hubspot_webchat_delivery_identifier,
            },
        },
    )
    if payload is None:
        raise HubSpotAPIError(502, "HubSpot returned no channel account")
    return _account_from_payload(payload)


async def set_channel_account_authorized(channel_account_id: str, *, authorized: bool) -> HubSpotChannelAccount:
    channel_id = settings.hubspot_webchat_custom_channel_id
    payload = await _request(
        "PATCH",
        f"/conversations/custom-channels/2026-03/{channel_id}/channel-accounts/{channel_account_id}",
        json_body={
            "authorized": authorized,
            "name": settings.hubspot_webchat_channel_account_name,
        },
    )
    if payload is None:
        raise HubSpotAPIError(502, "HubSpot returned no channel account")
    return _account_from_payload(payload)


async def ensure_channel_account(channel_account_id: str | None = None) -> HubSpotChannelAccount:
    accounts = await list_channel_accounts()
    if channel_account_id:
        for account in accounts:
            if account.id == channel_account_id:
                if account.authorized and not account.archived:
                    return account
                return await set_channel_account_authorized(account.id, authorized=True)

    for account in accounts:
        if (
            account.inbox_id == settings.hubspot_webchat_inbox_id
            and account.name == settings.hubspot_webchat_channel_account_name
            and not account.archived
        ):
            if account.authorized:
                return account
            return await set_channel_account_authorized(account.id, authorized=True)

    return await create_channel_account()


async def send_test_message(channel_account_id: str, *, widget_name: str, widget_public_id: str) -> dict[str, Any]:
    channel_id = settings.hubspot_webchat_custom_channel_id
    now = datetime.now(UTC).isoformat()
    payload = await _request(
        "POST",
        f"/conversations/custom-channels/2026-03/{channel_id}/messages",
        json_body={
            "channelAccountId": channel_account_id,
            "messageDirection": "INCOMING",
            "integrationThreadId": f"klai-widget-{widget_public_id}-test",
            "integrationIdempotencyId": str(uuid4()),
            "text": f"Testbericht vanuit {widget_name} naar HubSpot.",
            "timestamp": now,
            "senders": [
                {
                    "name": "Klai test visitor",
                    "deliveryIdentifier": {
                        "type": "CHANNEL_SPECIFIC_OPAQUE_ID",
                        "value": f"{widget_public_id}:test-visitor",
                    },
                }
            ],
            "recipients": [
                {
                    "name": "Voys support",
                    "deliveryIdentifier": {
                        "type": "CHANNEL_SPECIFIC_OPAQUE_ID",
                        "value": settings.hubspot_webchat_delivery_identifier,
                    },
                }
            ],
        },
    )
    if payload is None:
        raise HubSpotAPIError(502, "HubSpot returned no message")
    return payload


async def publish_incoming_message(
    *,
    channel_account_id: str,
    integration_thread_id: str,
    text: str,
    visitor_id: str,
    visitor_name: str = "Klai visitor",
    idempotency_id: str | None = None,
) -> dict[str, Any]:
    channel_id = settings.hubspot_webchat_custom_channel_id
    payload = await _request(
        "POST",
        f"/conversations/custom-channels/2026-03/{channel_id}/messages",
        json_body={
            "channelAccountId": channel_account_id,
            "messageDirection": "INCOMING",
            "integrationThreadId": integration_thread_id,
            "integrationIdempotencyId": idempotency_id or str(uuid4()),
            "text": text[:10000],
            "timestamp": datetime.now(UTC).isoformat(),
            "senders": [
                {
                    "name": visitor_name,
                    "deliveryIdentifier": {
                        "type": "CHANNEL_SPECIFIC_OPAQUE_ID",
                        "value": visitor_id,
                    },
                }
            ],
            "recipients": [
                {
                    "name": "Voys support",
                    "deliveryIdentifier": {
                        "type": "CHANNEL_SPECIFIC_OPAQUE_ID",
                        "value": settings.hubspot_webchat_delivery_identifier,
                    },
                }
            ],
        },
    )
    if payload is None:
        raise HubSpotAPIError(502, "HubSpot returned no message")
    return payload
