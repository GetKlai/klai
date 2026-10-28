"""Slack agent sessions around a DM turn (SPEC-SLACK-001 §9, agent container).

``agents.sessions.setStatus`` (statuses ``processing``/``active``, arguments
``channel_id`` + ``thread_ts``) shows Slack's own loading state, and
``agents.sessions.rename`` titles the session. Neither is available on every
Slack plan, and the exact error on a free plan is still to be captured in the
spike of §10, so any failure marks the team as unsupported for the life of the
process and the caller falls back to the placeholder message. Set ``ENABLED``
to False to switch the whole path off.
"""

from __future__ import annotations

import structlog
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = structlog.get_logger()

ENABLED = True
TITLE_MAX = 40

_unsupported: set[str] = set()


def title_from(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1].rstrip() + "…"


async def start(client: AsyncWebClient, team_id: str, channel_id: str, thread_ts: str) -> bool:
    """Set the session to processing. False = use the placeholder path instead."""
    if not ENABLED or team_id in _unsupported:
        return False
    try:
        await client.agents_sessions_setStatus(
            channel_id=channel_id, thread_ts=thread_ts, status="processing"
        )
    except SlackApiError as exc:
        _unsupported.add(team_id)
        logger.warning(
            "slack_sessions_unsupported",
            team_id=team_id,
            error=exc.response.get("error"),
            exc_info=True,
        )
        return False
    return True


async def finish(
    client: AsyncWebClient, channel_id: str, thread_ts: str, title: str | None
) -> None:
    """Back to active, and give a new session its title. Failures only cost the cosmetics."""
    try:
        await client.agents_sessions_setStatus(
            channel_id=channel_id, thread_ts=thread_ts, status="active"
        )
        if title:
            await client.agents_sessions_rename(
                channel_id=channel_id, thread_ts=thread_ts, title=title
            )
    except SlackApiError:
        logger.warning("slack_sessions_finish_failed", exc_info=True)
