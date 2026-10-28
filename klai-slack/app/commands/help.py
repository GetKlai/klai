"""`/klai help` (and any unknown subcommand): what Klai does and what it searches here."""

from __future__ import annotations

from app.clients.partner import PartnerError
from app.commands.context import CommandContext
from app.conversation.i18n import t
from app.conversation.render import scope_label
from app.conversation.turn import partner_error_text
from app.services.allowed_kbs import NotLinked
from app.services.scope import channel_scope
from app.services.slack_users import user_lang


async def channel_scope_text(lang: str, team_id: str, channel_id: str) -> str:
    try:
        kbs, effective = await channel_scope(team_id, channel_id)
    except NotLinked:
        return t(lang, "not_linked")
    except PartnerError as exc:
        return partner_error_text(exc, lang)
    if not kbs:
        return t(lang, "no_kbs")
    if effective == []:
        return t(lang, "empty_scope")
    names = None if effective is None else [kb.name for kb in kbs if kb.id in effective]
    return scope_label(lang, names)


async def run(ctx: CommandContext) -> None:
    team_id: str = ctx.command["team_id"]
    lang = await user_lang(ctx.client, ctx.command["user_id"])
    scope = await channel_scope_text(lang, team_id, ctx.command["channel_id"])
    home_url = f"slack://app?team={team_id}&id={ctx.command.get('api_app_id', '')}&tab=home"
    await ctx.respond(
        text=t(lang, "help", scope=scope, home_url=home_url), response_type="ephemeral"
    )
