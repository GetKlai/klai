"""Slack user lookups: the admin check of `/klai connect` and `/klai kb`, and the UI language."""

from __future__ import annotations

import time

import structlog
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = structlog.get_logger()

LOCALE_TTL_S = 3600.0
_langs: dict[str, tuple[float, str]] = {}


async def is_workspace_admin(client: AsyncWebClient, user_id: str) -> bool:
    """True for workspace admins and owners. Raises SlackApiError when users.info fails."""
    response = await client.users_info(user=user_id)
    user = response.get("user") or {}
    return bool(user.get("is_admin") or user.get("is_owner") or user.get("is_primary_owner"))


async def user_lang(client: AsyncWebClient, user_id: str) -> str:
    """Dutch for a Dutch or unknown locale, English for any other; cached per user for 1 h.

    A failing ``users.info`` yields Dutch without caching, because a language
    guess must never block an answer.
    """
    cached = _langs.get(user_id)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        response = await client.users_info(user=user_id, include_locale=True)
    except SlackApiError:
        logger.warning("slack_users_info_locale_failed", slack_user_id=user_id, exc_info=True)
        return "nl"
    locale = str((response.get("user") or {}).get("locale") or "")
    lang = "en" if locale and not locale.lower().startswith("nl") else "nl"
    _langs[user_id] = (now + LOCALE_TTL_S, lang)
    return lang
