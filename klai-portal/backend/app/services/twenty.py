"""Minimal Twenty CRM REST client.

SPEC-LAUNCH-SOFTLAUNCH-001 B-2 sub-batch 3.

The website (klai-website/src/pages/api/waitlist.ts) creates the deal +
person + company entries when a visitor submits the waitlist form. This
client is the read+update side: portal-api polls Twenty for waitlist
opportunities in specific stages and updates the stage after dispatch.

Scope kept deliberately small. Only the endpoints actually used by the
waitlist poller are wrapped:

- ``list_waitlist_opportunities_in_stage(stage)`` — fetch all deals in a
  given stage. Used to enumerate NEW (needs confirmation mail) and
  INVITED (needs invite mail).
- ``get_person(person_id)`` — resolve a deal's pointOfContact into name +
  email. Twenty's REST list endpoint does not expand FKs by default.
- ``get_company(company_id)`` — same idea for company name.
- ``update_opportunity_stage(opportunity_id, new_stage)`` — flag a deal
  as dispatched so the next poll cycle does not double-send.

If ``settings.twenty_url`` is empty the client raises
``TwentyUnavailable`` on every call — caller must check
``is_configured()`` first and skip its work cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Stage values used by the waitlist flow. These names must match the stage
# strings configured in the Twenty UI. NEW is Twenty's default for new
# deals; the others are Klai-specific and may need to be added in Twenty
# admin before the poller can transition deals into them.
STAGE_NEW = "NEW"
STAGE_CONFIRMATION_SENT = "CONFIRMATION_SENT"
STAGE_INVITED = "INVITED"
STAGE_INVITED_SENT = "INVITED_SENT"
STAGE_WON = "WON"
STAGE_UNSUBSCRIBED = "UNSUBSCRIBED"
WAITLIST_DEAL_NAME_SEPARATOR = "\N{EN DASH}"


class TwentyUnavailable(RuntimeError):
    """Raised when Twenty CRM is not configured or unreachable."""


@dataclass(frozen=True)
class WaitlistDeal:
    """Normalised view of a Twenty CRM opportunity for the waitlist flow."""

    opportunity_id: str
    stage: str
    name: str  # the recipient's first name (best-effort)
    email: str
    company: str


def is_configured() -> bool:
    """Return True iff Twenty is fully configured at startup."""
    return bool(settings.twenty_url and settings.twenty_api_key)


def _client() -> httpx.AsyncClient:
    """Return a configured httpx client for Twenty.

    Raises:
        TwentyUnavailable: when the URL or API key is missing.
    """
    if not is_configured():
        raise TwentyUnavailable("Twenty CRM not configured - set TWENTY_URL and TWENTY_API_KEY in SOPS.")
    return httpx.AsyncClient(
        base_url=settings.twenty_url,
        headers={
            "Authorization": f"Bearer {settings.twenty_api_key}",
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )


async def _get_json(client: httpx.AsyncClient, path: str) -> Any:
    resp = await client.get(path)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        logger.warning(
            "twenty_get_4xx5xx",
            path=path,
            status=resp.status_code,
            body=resp.text[:300],
        )
        return None
    return resp.json()


async def list_waitlist_opportunities_in_stage(stage: str) -> list[dict[str, Any]]:
    """Return raw opportunity rows in the given stage.

    Filters by ``name[like]:Waitlist%`` so the poller only acts on rows
    created by the website's waitlist endpoint - never on other deal
    pipelines an operator may run in the same Twenty workspace. The
    waitlist endpoint always names deals ``"Waitlist <en dash> <Company>"``
    or ``"Waitlist <en dash> <Name>"`` (see
    klai-website/src/pages/api/waitlist.ts).
    """
    async with _client() as client:
        # Twenty REST filter syntax: ?filter=field[op]:value, combinable
        # with comma. ``name[startsWith]:Waitlist`` would also work; using
        # ``like`` for tolerance against minor naming variations.
        path = "/rest/opportunities"
        params = f"filter=stage[eq]:{stage},name[like]:Waitlist%25&limit=100"
        data = await _get_json(client, f"{path}?{params}")
        if not isinstance(data, dict):
            return []
        # Twenty returns either {data: {opportunities: [...]}} or
        # {data: [...]} depending on the resource. Normalise.
        body = data.get("data")
        if isinstance(body, dict):
            items = body.get("opportunities", [])
        elif isinstance(body, list):
            items = body
        else:
            items = []
        return [r for r in items if isinstance(r, dict)]


async def get_person(client: httpx.AsyncClient, person_id: str) -> dict[str, Any] | None:
    data = await _get_json(client, f"/rest/people/{person_id}")
    if not isinstance(data, dict):
        return None
    body = data.get("data")
    if isinstance(body, dict):
        # Twenty wraps under {data: {person: {...}}}
        return body.get("person") if isinstance(body.get("person"), dict) else body
    return None


async def get_company(client: httpx.AsyncClient, company_id: str) -> dict[str, Any] | None:
    data = await _get_json(client, f"/rest/companies/{company_id}")
    if not isinstance(data, dict):
        return None
    body = data.get("data")
    if isinstance(body, dict):
        return body.get("company") if isinstance(body.get("company"), dict) else body
    return None


async def resolve_deal(opportunity: dict[str, Any]) -> WaitlistDeal | None:
    """Normalise a Twenty opportunity into a WaitlistDeal.

    Returns None if any of the required fields cannot be resolved.
    Logs at INFO so the poller can carry on with other deals.
    """
    opportunity_id = opportunity.get("id")
    stage = opportunity.get("stage") or ""
    person_id = opportunity.get("pointOfContactId")
    company_id = opportunity.get("companyId")
    if not opportunity_id or not person_id:
        logger.info(
            "waitlist_deal_missing_fields",
            opportunity_id=opportunity_id,
            has_person=bool(person_id),
        )
        return None

    async with _client() as client:
        person = await get_person(client, person_id)
        if person is None:
            logger.info("waitlist_deal_person_not_found", person_id=person_id)
            return None

        # Twenty's person model: name.firstName + name.lastName, emails.primaryEmail
        name_obj = person.get("name") or {}
        first_name = (name_obj.get("firstName") or "").strip()
        emails = person.get("emails") or {}
        email = (emails.get("primaryEmail") or "").strip()
        if not email:
            logger.info("waitlist_deal_email_missing", person_id=person_id)
            return None

        company_name = ""
        if company_id:
            company = await get_company(client, company_id)
            if isinstance(company, dict):
                company_name = (company.get("name") or "").strip()
        if not company_name:
            # Fall back to the deal name: "Waitlist <en dash> <Company>".
            deal_name = opportunity.get("name") or ""
            if WAITLIST_DEAL_NAME_SEPARATOR in deal_name:
                company_name = deal_name.split(WAITLIST_DEAL_NAME_SEPARATOR, 1)[1].strip()
            else:
                company_name = first_name or "your company"

    return WaitlistDeal(
        opportunity_id=str(opportunity_id),
        stage=str(stage),
        name=first_name or "there",
        email=email,
        company=company_name,
    )


async def update_opportunity_stage(opportunity_id: str, new_stage: str) -> bool:
    """Transition an opportunity to a new stage.

    Returns True on success. Logs and returns False on any HTTP error so
    the poller can skip this deal and retry next cycle rather than
    blocking the whole queue.
    """
    async with _client() as client:
        resp = await client.patch(
            f"/rest/opportunities/{opportunity_id}",
            json={"stage": new_stage},
        )
        if resp.status_code >= 400:
            logger.warning(
                "twenty_update_stage_failed",
                opportunity_id=opportunity_id,
                new_stage=new_stage,
                status=resp.status_code,
                body=resp.text[:300],
            )
            return False
    return True
