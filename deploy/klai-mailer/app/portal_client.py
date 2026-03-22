"""
Lightweight client for the klai-portal internal API.

Used only to resolve a user's preferred language by email address so that
klai-mailer can append ?lang=<lang> to email action URLs.

Degrades gracefully: returns None on any error. The caller logs a warning and
sends the email without a lang parameter (browser/localStorage fallback).
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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
    except Exception as exc:
        logger.warning("Could not fetch user language from portal for %s: %s", email, exc)
        return None
