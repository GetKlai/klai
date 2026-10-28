"""Slack user checks shared by the admin-only commands (`/klai connect`, `/klai kb`)."""

from __future__ import annotations

from slack_sdk.web.async_client import AsyncWebClient


async def is_workspace_admin(client: AsyncWebClient, user_id: str) -> bool:
    """True for workspace admins and owners. Raises SlackApiError when users.info fails."""
    response = await client.users_info(user=user_id)
    user = response.get("user") or {}
    return bool(user.get("is_admin") or user.get("is_owner") or user.get("is_primary_owner"))
