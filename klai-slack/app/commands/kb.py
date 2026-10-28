"""`/klai kb`: a workspace admin sets the channel default, layer 2 of SPEC-SLACK-001 §5.

The modal opens with a loading view first (the trigger expires after 3 s), the
admin check runs after that, and the submission is validated again server-side
in ``conversation.listeners.on_channel_kb``.
"""

from __future__ import annotations

import structlog
from slack_sdk.errors import SlackApiError

from app import db, repo
from app.clients.partner import PartnerError
from app.commands.context import CommandContext
from app.conversation.i18n import t
from app.conversation.slack_io import open_then_update
from app.conversation.turn import partner_error_text
from app.conversation.views import CALLBACK_CHANNEL_KB, View, kb_view, message_view
from app.services.allowed_kbs import NotLinked, allowed_kbs
from app.services.slack_users import is_workspace_admin, user_lang

logger = structlog.get_logger()


async def build_view(
    ctx: CommandContext, lang: str, team_id: str, channel_id: str, user_id: str
) -> View:
    try:
        admin = await is_workspace_admin(ctx.client, user_id)
    except SlackApiError:
        logger.exception("slack_kb_users_info_failed")
        return message_view(lang, t(lang, "slack_error"))
    if not admin:
        logger.info("slack_kb_not_admin", slack_user_id=user_id)
        return message_view(lang, t(lang, "kb_admin_only"))
    try:
        kbs = await allowed_kbs(team_id)
    except NotLinked:
        return message_view(lang, t(lang, "not_linked"))
    except PartnerError as exc:
        return message_view(lang, partner_error_text(exc, lang))
    if not kbs:
        return message_view(lang, t(lang, "no_kbs"))
    async with db.session() as s:
        current = await repo.get_channel_default(s, team_id, channel_id)
    return kb_view(
        lang,
        callback_id=CALLBACK_CHANNEL_KB,
        kbs=kbs,
        initial_ids=current,
        metadata={"channel": channel_id},
    )


async def run(ctx: CommandContext) -> None:
    team_id: str = ctx.command["team_id"]
    channel_id: str = ctx.command["channel_id"]
    user_id: str = ctx.command["user_id"]
    structlog.contextvars.bind_contextvars(team_id=team_id, channel_id=channel_id)
    lang = await user_lang(ctx.client, user_id)
    await open_then_update(
        ctx.client,
        ctx.command["trigger_id"],
        lang,
        lambda: build_view(ctx, lang, team_id, channel_id, user_id),
    )
