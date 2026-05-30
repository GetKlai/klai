"""listmonk API client for Klai mailing sync.

The client is intentionally small: it owns subscriber upsert, list-membership
adds, and transactional template sends. Auth/security emails stay in Zitadel /
klai-mailer; listmonk is for marketing, product updates, and operator-triggered
non-auth mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

PROTECTED_SUBSCRIBER_STATUSES = {"blocklisted", "blacklisted", "disabled", "unsubscribed"}


class ListmonkUnavailable(RuntimeError):
    """Raised when listmonk is not configured."""


class ListmonkAPIError(RuntimeError):
    """Raised when listmonk returns an unexpected 4xx/5xx."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:500]
        super().__init__(f"listmonk API returned {status_code}: {self.body}")


@dataclass(frozen=True)
class ListmonkSyncResult:
    subscriber_id: int
    lists_added: list[int]


@dataclass(frozen=True)
class ListmonkSendResult:
    sent: bool
    template_id: int
    sent_to: str


def is_configured() -> bool:
    return bool(
        settings.listmonk_url.strip()
        and settings.listmonk_api_user.strip()
        and settings.listmonk_api_token.strip()
    )


def _client() -> httpx.AsyncClient:
    if not is_configured():
        raise ListmonkUnavailable("listmonk is not configured")
    return httpx.AsyncClient(
        base_url=settings.listmonk_url.rstrip("/"),
        auth=(settings.listmonk_api_user, settings.listmonk_api_token),
        timeout=15.0,
    )


def list_ids_for_audiences(audiences: list[str]) -> list[int]:
    mapping = {
        "crm_selected": settings.listmonk_list_crm_selected_id,
        "signups": settings.listmonk_list_signups_id,
        "users": settings.listmonk_list_users_id,
        "updates_opt_in": settings.listmonk_list_updates_opt_in_id,
    }
    unknown = sorted(set(audiences) - set(mapping))
    if unknown:
        raise ValueError(f"Unknown mailing audience(s): {', '.join(unknown)}")
    return sorted({mapping[audience] for audience in audiences if mapping[audience] > 0})


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _is_duplicate_response(resp: httpx.Response) -> bool:
    if resp.status_code == 409:
        return True
    if resp.status_code != 400:
        return False
    return any(token in resp.text.lower() for token in ("duplicate", "already exists", "unique"))


def _data(payload: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get("data")
    return None


def _results(payload: Any) -> list[dict[str, Any]]:
    data = _data(payload)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return [item for item in data["results"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _subscriber_id(subscriber: dict[str, Any]) -> int:
    raw_id = subscriber.get("id")
    if isinstance(raw_id, int):
        return raw_id
    if isinstance(raw_id, str) and raw_id.isdigit():
        return int(raw_id)
    raise ListmonkAPIError(502, "listmonk subscriber response did not include an id")


def _list_state(subscriber: dict[str, Any]) -> tuple[set[int], set[int]]:
    subscribed: set[int] = set()
    unsubscribed: set[int] = set()
    for raw in subscriber.get("lists") or []:
        if isinstance(raw, int):
            subscribed.add(raw)
            continue
        if isinstance(raw, dict):
            raw_id = raw.get("id")
            list_id = (
                raw_id
                if isinstance(raw_id, int)
                else int(raw_id)
                if isinstance(raw_id, str) and raw_id.isdigit()
                else None
            )
            if list_id is None:
                continue
            subscribed.add(list_id)
            if str(raw.get("subscription_status", "")).lower() == "unsubscribed":
                unsubscribed.add(list_id)
    return subscribed, unsubscribed


def _attribs(
    *,
    source: str,
    audiences: list[str],
    company: str | None = None,
    twenty_person_id: str | None = None,
    portal_user_id: int | None = None,
    zitadel_user_id: str | None = None,
    org_id: int | None = None,
    product: str | None = None,
    marketing_consent: bool | None = None,
) -> dict[str, Any]:
    attribs: dict[str, Any] = {
        "source": source,
        "audiences": sorted(set(audiences)),
    }
    optional: dict[str, Any] = {
        "company": company,
        "twentyPersonId": twenty_person_id,
        "portalUserId": portal_user_id,
        "zitadelUserId": zitadel_user_id,
        "orgId": org_id,
        "product": product,
        "marketingConsent": marketing_consent,
    }
    for key, value in optional.items():
        if value is not None and value != "":
            attribs[key] = value
    return attribs


async def _find_subscriber(client: httpx.AsyncClient, email: str) -> dict[str, Any] | None:
    resp = await client.get(
        "/api/subscribers",
        params={
            "query": f"subscribers.email = '{_sql_string(email)}'",
            "per_page": 1,
        },
    )
    if resp.status_code >= 400:
        raise ListmonkAPIError(resp.status_code, resp.text)
    results = _results(resp.json())
    return results[0] if results else None


async def _patch_subscriber(
    client: httpx.AsyncClient,
    subscriber_id: int,
    *,
    name: str,
    attribs: dict[str, Any],
) -> None:
    resp = await client.patch(
        f"/api/subscribers/{subscriber_id}",
        json={
            "name": name,
            "attribs": attribs,
        },
    )
    if resp.status_code >= 400:
        raise ListmonkAPIError(resp.status_code, resp.text)


async def _add_lists(client: httpx.AsyncClient, subscriber_id: int, list_ids: list[int]) -> None:
    if not list_ids:
        return
    resp = await client.put(
        "/api/subscribers/lists",
        json={
            "ids": [subscriber_id],
            "action": "add",
            "target_list_ids": list_ids,
            "status": "confirmed",
        },
    )
    if resp.status_code >= 400:
        raise ListmonkAPIError(resp.status_code, resp.text)


async def sync_contact(
    *,
    email: str,
    name: str | None,
    source: str,
    audiences: list[str],
    company: str | None = None,
    twenty_person_id: str | None = None,
    portal_user_id: int | None = None,
    zitadel_user_id: str | None = None,
    org_id: int | None = None,
    product: str | None = None,
    marketing_consent: bool | None = None,
) -> ListmonkSyncResult:
    """Upsert a subscriber and add non-unsubscribed list memberships."""
    email_norm = _normalise_email(email)
    display_name = (name or "").strip() or email_norm
    target_list_ids = list_ids_for_audiences(audiences)
    attribs = _attribs(
        source=source,
        audiences=audiences,
        company=company,
        twenty_person_id=twenty_person_id,
        portal_user_id=portal_user_id,
        zitadel_user_id=zitadel_user_id,
        org_id=org_id,
        product=product,
        marketing_consent=marketing_consent,
    )

    async with _client() as client:
        resp = await client.post(
            "/api/subscribers",
            json={
                "email": email_norm,
                "name": display_name,
                "status": "enabled",
                "lists": target_list_ids,
                "attribs": attribs,
                "preconfirm_subscriptions": True,
            },
        )
        if resp.status_code < 400:
            subscriber = _data(resp.json())
            if not isinstance(subscriber, dict):
                raise ListmonkAPIError(502, "listmonk subscriber response was malformed")
            return ListmonkSyncResult(subscriber_id=_subscriber_id(subscriber), lists_added=target_list_ids)

        if not _is_duplicate_response(resp):
            raise ListmonkAPIError(resp.status_code, resp.text)

        existing = await _find_subscriber(client, email_norm)
        if existing is None:
            raise ListmonkAPIError(resp.status_code, resp.text)

        subscriber_id = _subscriber_id(existing)
        existing_attribs = existing.get("attribs") if isinstance(existing.get("attribs"), dict) else {}
        await _patch_subscriber(
            client,
            subscriber_id,
            name=display_name,
            attribs={**existing_attribs, **attribs},
        )

        status = str(existing.get("status", "")).lower()
        if status in PROTECTED_SUBSCRIBER_STATUSES:
            return ListmonkSyncResult(subscriber_id=subscriber_id, lists_added=[])

        existing_list_ids, unsubscribed_list_ids = _list_state(existing)
        list_ids_to_add = [
            list_id
            for list_id in target_list_ids
            if list_id not in existing_list_ids and list_id not in unsubscribed_list_ids
        ]
        await _add_lists(client, subscriber_id, list_ids_to_add)
        return ListmonkSyncResult(subscriber_id=subscriber_id, lists_added=list_ids_to_add)


async def sync_contact_best_effort(**kwargs: Any) -> ListmonkSyncResult | None:
    """Sync without failing the primary product flow."""
    if not is_configured():
        logger.info("listmonk_sync_skipped_not_configured")
        return None
    try:
        return await sync_contact(**kwargs)
    except Exception:
        logger.warning("listmonk_sync_failed", exc_info=True)
        return None


async def sync_portal_user_best_effort(
    *,
    email: str | None,
    name: str | None = None,
    company: str | None = None,
    org_id: int | None = None,
    portal_user_id: int | None = None,
    zitadel_user_id: str | None = None,
    source: str,
) -> ListmonkSyncResult | None:
    """Best-effort sync for real portal/Zitadel users."""
    if not email:
        logger.info("listmonk_user_sync_skipped_no_email", source=source, org_id=org_id)
        return None
    return await sync_contact_best_effort(
        email=email,
        name=name or email,
        source=source,
        audiences=["users"],
        company=company,
        org_id=org_id,
        portal_user_id=portal_user_id,
        zitadel_user_id=zitadel_user_id,
    )


async def send_onboarding_invite(*, email: str, name: str, cal_url: str) -> ListmonkSendResult:
    """Send the v1 onboarding invite through listmonk transactional mail."""
    template_id = settings.listmonk_tx_onboarding_template_id
    if template_id <= 0:
        raise ListmonkUnavailable("listmonk onboarding transactional template is not configured")
    email_norm = _normalise_email(email)
    async with _client() as client:
        resp = await client.post(
            "/api/tx",
            json={
                "subscriber_mode": "fallback",
                "subscriber_emails": [email_norm],
                "template_id": template_id,
                "data": {
                    "name": name,
                    "cal_url": cal_url,
                },
                "content_type": "html",
            },
        )
        if resp.status_code >= 400:
            raise ListmonkAPIError(resp.status_code, resp.text)
    return ListmonkSendResult(sent=True, template_id=template_id, sent_to=email_norm)
