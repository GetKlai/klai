"""Small Slack Web API helpers shared by the listeners and the `/klai` subcommands."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.conversation.views import View, loading_view

logger = structlog.get_logger()


async def ephemeral(
    client: AsyncWebClient, channel_id: str, user_id: str, text: str, thread_ts: str | None = None
) -> None:
    try:
        await client.chat_postEphemeral(
            channel=channel_id, user=user_id, text=text, thread_ts=thread_ts
        )
    except SlackApiError:
        # not_in_channel and friends: Slack accepts no visible message here at all.
        logger.warning("slack_ephemeral_failed", channel_id=channel_id, exc_info=True)


async def open_then_update(
    client: AsyncWebClient, trigger_id: str, lang: str, build: Callable[[], Awaitable[View]]
) -> None:
    """Open a loading modal at once, then fill it.

    A ``trigger_id`` expires 3 seconds after the click, while building the real
    view can take a Partner API round trip (10 s timeout), so the modal is opened
    first and updated when the content is ready.
    """
    try:
        opened = await client.views_open(trigger_id=trigger_id, view=loading_view(lang))
    except SlackApiError:
        logger.exception("slack_views_open_failed")
        return
    view = await build()
    await client.views_update(view_id=str(opened.get("view", {}).get("id")), view=view)
