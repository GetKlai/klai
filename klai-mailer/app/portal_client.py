"""
Lightweight client for the klai-portal internal API.

Two responsibilities:
1. Resolve a user's preferred language by email (Zitadel /notify flow).
   Degrades gracefully — returns None on any error.
2. Resolve an organisation's admin email by org_id (REQ-3.1). This one
   fails CLOSED (raises) because the recipient binding for
   `join_request_admin` cannot be proved without it.
"""

from __future__ import annotations

import httpx
import structlog

from app.config import settings

logger = structlog.get_logger()


async def get_user_language(email: str) -> str | None:
    """Return the preferred language ("nl" or "en") for the given email.

    Calls the portal internal API. Returns None if:
    - portal_internal_secret is not configured
    - the portal is unreachable
    - the request fails for any reason
    """
    if not settings.portal_internal_secret:
        return None

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                f"{settings.portal_api_url}/internal/user-language",
                params={"email": email},
                headers={"Authorization": f"Bearer {settings.portal_internal_secret}"},
            )
            resp.raise_for_status()
            return resp.json()["preferred_language"]
    except Exception:
        logger.warning("portal_user_language_lookup_failed", email=email, exc_info=True)
        return None


class PortalLookupError(RuntimeError):
    """Raised when a required portal-api callback cannot complete.

    Distinct from `get_user_language`'s silent-None path because a recipient-
    binding callback is a hard dependency — `join_request_admin` cannot be
    delivered without the resolved admin email. Callers map this to HTTP 503.
    """


async def resolve_org_admin_email(org_id: int) -> str:
    """Return the admin email for `org_id` via portal-api callback.

    REQ-3.1 / REQ-3.4: 3.0s timeout; any failure raises `PortalLookupError`
    so the handler can return HTTP 503 `{"detail": "recipient lookup
    unavailable"}`. Fail-closed is correct: without the callback the
    service cannot prove the recipient is the legitimate admin.
    """
    if not settings.portal_internal_secret:
        raise PortalLookupError("portal_internal_secret not configured")

    url = f"{settings.portal_api_url}/internal/org/{int(org_id)}/admin-email"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.portal_internal_secret}"},
            )
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        logger.error(
            "mailer_recipient_lookup_failed",
            org_id=org_id,
            error=str(exc),
        )
        raise PortalLookupError(str(exc)) from exc
    except Exception as exc:
        # JSON decode / schema error also fail-closed
        logger.exception(
            "mailer_recipient_lookup_failed",
            org_id=org_id,
            error=str(exc),
        )
        raise PortalLookupError(str(exc)) from exc

    admin_email = payload.get("admin_email")
    if not admin_email or not isinstance(admin_email, str):
        logger.error(
            "mailer_recipient_lookup_failed",
            org_id=org_id,
            reason="missing_admin_email_in_response",
        )
        raise PortalLookupError("missing admin_email in portal-api response")

    return admin_email
