"""Bolt app: OAuth install, dedupe, uninstall, `/klai`, conversation (SPEC-SLACK-001 §7.4, §9)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from slack_bolt import BoltResponse
from slack_bolt.async_app import AsyncApp
from slack_bolt.oauth.async_oauth_settings import AsyncOAuthSettings
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.oauth.installation_store.async_installation_store import AsyncInstallationStore
from slack_sdk.oauth.state_store.async_state_store import AsyncOAuthStateStore

from app import commands, db, portal_link, repo
from app.clients.portal import PortalClient, PortalUnavailable
from app.config import Settings
from app.conversation import listeners

logger = structlog.get_logger()

BOT_SCOPES = [
    "app_mentions:read",
    "chat:write",
    "im:history",
    "im:write",
    "channels:history",
    "groups:history",
    "commands",
    "users:read",
    "assistant:write",
]
INSTALL_PATH = "/slack/install"
CONNECT_PATH = "/slack/connect"
REDIRECT_PATH = "/slack/oauth_redirect"


async def dedupe_events(
    body: dict[str, Any], request: AsyncBoltRequest, next_: Callable[[], Awaitable[None]]
) -> BoltResponse | None:
    """Ack and drop a redelivered event (Slack retries carry the same ``event_id``).

    Insert-first: the row is written before any handler runs, so a retry that
    arrives while the first delivery is still being processed is dropped too.
    """
    event_id = body.get("event_id") if body.get("type") == "event_callback" else None
    if event_id:
        async with db.session() as s:
            first_delivery = await repo.mark_event_processed(s, event_id)
        if not first_delivery:
            logger.info(
                "slack_event_duplicate_skipped",
                event_id=event_id,
                retry_num=(request.headers.get("x-slack-retry-num") or [None])[0],
            )
            return BoltResponse(status=200, body="")
    await next_()
    return None


async def teardown_team(team_id: str, portal: PortalClient, reason: str) -> None:
    """Slack side gone (uninstall, revoked bot token): release the portal link, purge rows."""
    try:
        await portal.unlink(team_id)
    except PortalUnavailable:
        # Loud, not silent: the portal keeps showing "connected" until an admin disconnects.
        logger.exception("slack_teardown_portal_unlink_failed", team_id=team_id, reason=reason)
    async with db.session() as s:
        await repo.purge_team(s, team_id)
    logger.info("slack_team_purged", team_id=team_id, reason=reason)


def build_oauth_settings(
    settings: Settings,
    installation_store: AsyncInstallationStore,
    state_store: AsyncOAuthStateStore,
    portal: PortalClient,
) -> AsyncOAuthSettings:
    """Shared by Bolt's install flow and ``/slack/connect``, so both send Slack the same
    scopes, redirect URI and state."""
    return AsyncOAuthSettings(
        client_id=settings.slack_client_id,
        client_secret=settings.slack_client_secret,
        scopes=BOT_SCOPES,
        install_path=INSTALL_PATH,
        redirect_uri_path=REDIRECT_PATH,
        redirect_uri=f"{settings.public_base_url.rstrip('/')}{REDIRECT_PATH}",
        installation_store=installation_store,
        installation_store_bot_only=True,
        state_store=state_store,
        callback_options=portal_link.callback_options(portal, settings.portal_internal_secret),
    )


def build_slack_app(
    settings: Settings, oauth_settings: AsyncOAuthSettings, portal: PortalClient
) -> AsyncApp:
    app = AsyncApp(
        signing_secret=settings.slack_signing_secret,
        process_before_response=False,
        oauth_settings=oauth_settings,
    )
    app.middleware(dedupe_events)

    async def on_app_uninstalled(body: dict[str, Any]) -> None:
        await teardown_team(body["team_id"], portal, "app_uninstalled")

    async def on_tokens_revoked(body: dict[str, Any], event: dict[str, Any]) -> None:
        # Only a revoked bot token disables the team; klai-slack holds no user tokens.
        if event.get("tokens", {}).get("bot"):
            await teardown_team(body["team_id"], portal, "tokens_revoked")

    app.event("app_uninstalled")(on_app_uninstalled)
    app.event("tokens_revoked")(on_tokens_revoked)
    commands.register(app)
    listeners.register(app)
    return app
