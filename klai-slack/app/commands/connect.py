"""`/klai connect <pk_live_…>`: link this Slack workspace to a Klai org (SPEC-SLACK-001 §9).

The key is never logged and never echoed back. Slack does not post a slash
command's text as a channel message, so there is nothing to delete afterwards;
the ack and every reply are ephemeral.
"""

from __future__ import annotations

import structlog
from slack_sdk.errors import SlackApiError

from app import db, repo
from app.clients.portal import (
    LinkConflict,
    LinkFeatureLocked,
    LinkKeyInvalid,
    LinkResult,
    PortalUnavailable,
)
from app.commands.context import CommandContext
from app.services.slack_users import is_workspace_admin

logger = structlog.get_logger()

ACK_TEXT = "Bezig met koppelen…"
KEY_PREFIX = "pk_live_"

USAGE = (
    "Gebruik: `/klai connect pk_live_…` met de Slack-sleutel uit de Klai-portal (Beheer > Slack)."
)
NOT_ADMIN = "Alleen beheerders en eigenaren van deze Slack-workspace kunnen Klai koppelen."
SLACK_ERROR = "Ik kon je Slack-rechten niet controleren. Probeer het over een paar minuten opnieuw."
ALREADY_LINKED = "Deze Slack-workspace is al gekoppeld aan Klai-organisatie *{org}*."
KEY_INVALID = (
    "Deze sleutel is onbekend of is geen Slack-sleutel. Maak een Slack-sleutel in de "
    "Klai-portal onder Beheer > Slack en plak die hier."
)
FEATURE_LOCKED = (
    "Slack is nog niet beschikbaar voor jullie Klai-organisatie. Neem contact op met Klai."
)
ORG_ALREADY_LINKED = "Deze Klai-organisatie is al aan een andere Slack-workspace gekoppeld."
TEAM_ALREADY_LINKED = "Deze Slack-workspace is al aan een Klai-organisatie gekoppeld."
PORTAL_UNAVAILABLE = "Klai is nu niet bereikbaar. Probeer het over een paar minuten opnieuw."
STORE_FAILED = "Koppelen is mislukt door een fout bij Klai. Probeer het opnieuw."
LINKED = (
    "Gekoppeld aan Klai-organisatie *{org}*. Klai zoekt in {count} vrijgegeven "
    "{kb_word}. Stel je vraag in een DM of noem @Klai in een kanaal."
)


async def _say(ctx: CommandContext, text: str) -> None:
    await ctx.respond(text=text, response_type="ephemeral")


async def run(ctx: CommandContext) -> None:
    team_id: str = ctx.command["team_id"]
    user_id: str = ctx.command["user_id"]
    api_key = ctx.args
    structlog.contextvars.bind_contextvars(team_id=team_id)

    if not api_key.startswith(KEY_PREFIX) or any(c.isspace() for c in api_key):
        await _say(ctx, USAGE)
        return

    try:
        admin = await is_workspace_admin(ctx.client, user_id)
    except SlackApiError:
        logger.exception("slack_connect_users_info_failed")
        await _say(ctx, SLACK_ERROR)
        return
    if not admin:
        logger.info("slack_connect_not_admin", slack_user_id=user_id)
        await _say(ctx, NOT_ADMIN)
        return

    async with db.session() as s:
        link = await repo.get_link(s, team_id)
        team_name = await repo.get_team_name(s, team_id)
    if link is not None and link.revoked_at is None:
        await _say(ctx, ALREADY_LINKED.format(org=link.org_name))
        return

    result = await _link(ctx, api_key, team_id, team_name or ctx.command.get("team_domain", ""))
    if result is None:
        return

    try:
        async with db.session() as s:
            await repo.save_link(
                s,
                team_id=team_id,
                org_id=result.org_id,
                org_name=result.org_name,
                api_key=api_key,
                key_id=result.key_id,
                linked_by_slack_user_id=user_id,
            )
    except Exception:
        # The portal already recorded the link; release it so the portal shows
        # "revoked" with a re-link action instead of a link this service lacks.
        logger.exception("slack_connect_store_failed", org_id=result.org_id)
        try:
            await ctx.portal.unlink(team_id)
        except PortalUnavailable:
            logger.exception("slack_connect_compensating_unlink_failed", org_id=result.org_id)
        await _say(ctx, STORE_FAILED)
        return

    count = len(result.kb_ids)
    logger.info(
        "slack_workspace_linked", org_id=result.org_id, key_id=result.key_id, kb_count=count
    )
    await _say(
        ctx,
        LINKED.format(
            org=result.org_name, count=count, kb_word="kennisbank" if count == 1 else "kennisbanken"
        ),
    )


async def _link(
    ctx: CommandContext, api_key: str, team_id: str, team_name: str
) -> LinkResult | None:
    user_id: str = ctx.command["user_id"]
    try:
        return await ctx.portal.link(api_key, team_id, team_name, user_id)
    except LinkKeyInvalid:
        message, reason = KEY_INVALID, "key_invalid"
    except LinkFeatureLocked:
        message, reason = FEATURE_LOCKED, "feature_locked"
    except LinkConflict as exc:
        message = ORG_ALREADY_LINKED if exc.code == "org_already_linked" else TEAM_ALREADY_LINKED
        reason = exc.code
    except PortalUnavailable:
        message, reason = PORTAL_UNAVAILABLE, "portal_unavailable"
    logger.warning("slack_connect_rejected", reason=reason)
    await _say(ctx, message)
    return None
