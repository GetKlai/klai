"""One question/answer turn in a Slack thread (SPEC-SLACK-001 §4, §5, §9).

Order of a turn: link → layer-1 KBs → effective scope → transcript → Partner
API → reply in the thread → record both turns. Every refusal on the way (not
linked, nothing released, empty intersection, 429, 5xx) becomes a visible
reply; an unexpected exception is logged and answered with a generic message,
so a question is never dropped in silence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import structlog
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app import db, repo
from app.clients.partner import (
    ChatResult,
    PartnerAuthError,
    PartnerError,
    PartnerRateLimited,
)
from app.conversation import sessions
from app.conversation.i18n import placeholder, t
from app.conversation.render import RenderedAnswer, render_answer, render_notice
from app.models import Turn
from app.services import allowed_kbs as layer1
from app.services.scope import resolve_kb_ids
from app.services.slack_users import user_lang

logger = structlog.get_logger()

HISTORY_TURNS = 20
# conversations.replies is throttled to 1 req/min for non-Marketplace apps (spec D7),
# so a thread the bot has not seen is read once, at most this many messages.
SEED_LIMIT = 15
PENDING_TTL_S = 3600.0
NOT_IN_CHANNEL = ("channel_not_found", "not_in_channel")


@dataclass(frozen=True)
class TurnRequest:
    team_id: str
    channel_id: str
    thread_ts: str
    message_ts: str
    user_id: str
    text: str
    is_dm: bool
    bot_user_id: str | None = None


@dataclass(frozen=True)
class Prepared:
    workspace: layer1.Workspace
    allowed_ids: list[int]
    kb_ids: list[int] | None
    kb_names: list[str]

    @property
    def searched_ids(self) -> list[int]:
        return self.allowed_ids if self.kb_ids is None else self.kb_ids


def thread_key(channel_id: str, thread_ts: str) -> str:
    return f"{channel_id}:{thread_ts}"


# (team_id, thread key) -> (expires_at, request) for the "Opnieuw proberen" button after a 429.
_pending: dict[tuple[str, str], tuple[float, TurnRequest]] = {}


def strip_mention(text: str, bot_user_id: str | None) -> str:
    return (text.replace(f"<@{bot_user_id}>", "") if bot_user_id else text).strip()


def partner_error_text(exc: PartnerError, lang: str) -> str:
    """The visible text for a refused or failed Partner API call, outside a turn too."""
    if isinstance(exc, PartnerRateLimited):
        if exc.retry_after:
            return t(lang, "rate_limited", seconds=exc.retry_after)
        return t(lang, "rate_limited_unknown")
    if isinstance(exc, PartnerAuthError):
        if exc.error_type == "permission_error":
            # The stored scope fell outside the key: same state as an empty intersection.
            return t(lang, "empty_scope")
        return t(lang, "key_refused", request_id=exc.request_id)
    return t(lang, "unavailable", request_id=exc.request_id)


def _notice_for(exc: PartnerError, lang: str, req: TurnRequest) -> RenderedAnswer:
    text = partner_error_text(exc, lang)
    if isinstance(exc, PartnerRateLimited):
        key = thread_key(req.channel_id, req.thread_ts)
        _pending[(req.team_id, key)] = (time.monotonic() + PENDING_TTL_S, req)
        return render_notice(text, retry_label=t(lang, "retry"), retry_value=key)
    if isinstance(exc, PartnerAuthError):
        layer1.invalidate(req.team_id)
        if exc.error_type == "permission_error":
            return render_notice(text, pick_label=t(lang, "pick_kbs"))
    return render_notice(text)


async def prepare(req: TurnRequest, lang: str) -> Prepared | RenderedAnswer:
    try:
        workspace = await layer1.get_workspace(req.team_id)
        kbs = await layer1.allowed_kbs(req.team_id, workspace)
    except layer1.NotLinked:
        return render_notice(t(lang, "not_linked"))
    except PartnerError as exc:
        return _notice_for(exc, lang, req)
    if not kbs:
        return render_notice(t(lang, "no_kbs"))
    allowed_ids = [kb.id for kb in kbs]
    async with db.session() as s:
        kb_ids = await resolve_kb_ids(s, req.team_id, req.channel_id, req.thread_ts, allowed_ids)
    if kb_ids == []:
        logger.info("slack_turn_empty_scope", team_id=req.team_id, channel_id=req.channel_id)
        return render_notice(t(lang, "empty_scope"), pick_label=t(lang, "pick_kbs"))
    names = {kb.id: kb.name for kb in kbs}
    return Prepared(
        workspace=workspace,
        allowed_ids=allowed_ids,
        kb_ids=kb_ids,
        # Spec §12 test 3: "Gezocht in" names the KBs, also when the scope is all of layer 1.
        kb_names=[names[i] for i in (allowed_ids if kb_ids is None else kb_ids)],
    )


def _alternate(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Merge consecutive messages of one role, so roles alternate as chat models expect."""
    merged: list[dict[str, str]] = []
    for message in messages:
        if merged and merged[-1]["role"] == message["role"]:
            merged[-1] = {
                "role": message["role"],
                "content": f"{merged[-1]['content']}\n\n{message['content']}",
            }
        else:
            merged.append(message)
    return merged


async def _seed_from_thread(client: AsyncWebClient, req: TurnRequest) -> list[dict[str, str]]:
    """History of a channel thread the bot joins mid-way; one conversations.replies call."""
    try:
        response = await client.conversations_replies(
            channel=req.channel_id, ts=req.thread_ts, limit=SEED_LIMIT
        )
    except SlackApiError:
        logger.warning("slack_thread_seed_failed", channel_id=req.channel_id, exc_info=True)
        return []
    history: list[dict[str, str]] = []
    for message in response.get("messages") or []:
        if message.get("ts") == req.message_ts or not message.get("text"):
            continue
        from_bot = bool(message.get("bot_id")) or message.get("user") == req.bot_user_id
        role: Literal["user", "assistant"] = "assistant" if from_bot else "user"
        content = strip_mention(message["text"], req.bot_user_id)
        if content:
            history.append({"role": role, "content": content})
    logger.info(
        "slack_thread_history_seeded", channel_id=req.channel_id, message_count=len(history)
    )
    return history


async def build_messages(
    client: AsyncWebClient, req: TurnRequest, turns: list[Turn]
) -> list[dict[str, str]]:
    history = [{"role": turn.role, "content": turn.text} for turn in turns[-HISTORY_TURNS:]]
    if not turns and not req.is_dm and req.thread_ts != req.message_ts:
        history = await _seed_from_thread(client, req)
    return _alternate([*history, {"role": "user", "content": req.text}])


async def answer(
    req: TurnRequest, prepared: Prepared, messages: list[dict[str, str]], lang: str
) -> tuple[RenderedAnswer, ChatResult | None]:
    try:
        result = await prepared.workspace.partner.chat(messages, prepared.kb_ids)
    except PartnerError as exc:
        logger.warning(
            "slack_turn_partner_refused",
            error=type(exc).__name__,
            status=exc.status,
            request_id=exc.request_id,
        )
        return _notice_for(exc, lang, req), None
    return render_answer(result, prepared.kb_names, lang), result


async def deliver(
    client: AsyncWebClient, req: TurnRequest, rendered: RenderedAnswer, placeholder_ts: str | None
) -> str | None:
    """Post (or replace the placeholder with) the reply in the thread; the first message's ts."""
    first, *rest = rendered.messages
    try:
        if placeholder_ts:
            await client.chat_update(
                channel=req.channel_id, ts=placeholder_ts, text=first.text, blocks=first.blocks
            )
            ts = placeholder_ts
        else:
            ts = await _post(client, req, first.text, first.blocks)
        for message in rest:
            await _post(client, req, message.text, message.blocks)
    except SlackApiError as exc:
        _log_post_failure(exc, req)
        return None
    return ts


async def _post(
    client: AsyncWebClient, req: TurnRequest, text: str, blocks: list[dict] | None = None
) -> str:
    response = await client.chat_postMessage(
        channel=req.channel_id,
        thread_ts=req.thread_ts,
        text=text,
        blocks=blocks,
        unfurl_links=False,
        unfurl_media=False,
    )
    return str(response["ts"])


def _log_post_failure(exc: SlackApiError, req: TurnRequest) -> None:
    error = exc.response.get("error")
    if error in NOT_IN_CHANNEL:
        # Nothing visible is possible: Slack refuses every post to this channel.
        logger.warning("slack_post_not_in_channel", channel_id=req.channel_id, error=error)
    else:
        logger.error("slack_post_failed", channel_id=req.channel_id, error=error, exc_info=exc)


async def handle_turn(client: AsyncWebClient, req: TurnRequest) -> None:
    structlog.contextvars.bind_contextvars(team_id=req.team_id, channel_id=req.channel_id)
    lang = "nl"
    try:
        lang = await user_lang(client, req.user_id)
        await _handle_turn(client, req, lang)
    except Exception:
        logger.exception("slack_turn_failed")
        try:
            await _post(client, req, t(lang, "unexpected"))
        except SlackApiError as exc:
            _log_post_failure(exc, req)


async def _handle_turn(client: AsyncWebClient, req: TurnRequest, lang: str) -> None:
    if not req.text:
        await deliver(client, req, render_notice(t(lang, "empty_question")), None)
        return
    outcome = await prepare(req, lang)
    if isinstance(outcome, RenderedAnswer):
        await deliver(client, req, outcome, None)
        return

    started = time.monotonic()
    session = req.is_dm and await sessions.start(client, req.team_id, req.channel_id, req.thread_ts)
    result: ChatResult | None = None
    first_turn = False
    try:
        placeholder_ts = None
        if not session:
            try:
                placeholder_ts = await _post(
                    client, req, placeholder(lang, len(outcome.searched_ids))
                )
            except SlackApiError as exc:
                _log_post_failure(exc, req)
                return
        async with db.session() as s:
            turns = await repo.list_turns(s, req.team_id, req.channel_id, req.thread_ts)
        first_turn = not turns
        messages = await build_messages(client, req, turns)
        rendered, result = await answer(req, outcome, messages, lang)
        answer_ts = await deliver(client, req, rendered, placeholder_ts)
        if result is not None and answer_ts is not None:
            await _record(req, outcome, result, answer_ts)
            logger.info(
                "slack_turn_answered",
                completion_id=result.completion_id,
                kb_count=len(outcome.searched_ids),
                scoped=outcome.kb_ids is not None,
                source_count=len(result.sources),
                history_messages=len(messages) - 1,
                message_count=len(rendered.messages),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
    finally:
        if session:
            title = sessions.title_from(req.text) if first_turn and result is not None else None
            await sessions.finish(client, req.channel_id, req.thread_ts, title)


async def _record(req: TurnRequest, prepared: Prepared, result: ChatResult, answer_ts: str) -> None:
    async with db.session() as s:
        rows: tuple[tuple[Literal["user", "assistant"], str, str], ...] = (
            ("user", req.text, req.message_ts),
            ("assistant", result.text, answer_ts),
        )
        for role, text, ts in rows:
            await repo.record_turn(
                s,
                team_id=req.team_id,
                channel_id=req.channel_id,
                thread_ts=req.thread_ts,
                message_ts=ts,
                role=role,
                text=text,
                completion_id=result.completion_id if role == "assistant" else None,
                kb_ids=prepared.searched_ids,
            )


async def retry(client: AsyncWebClient, team_id: str, key: str) -> bool:
    """Rerun a turn that hit a 429. False when it expired (or the process restarted)."""
    entry = _pending.pop((team_id, key), None)
    if entry is None or entry[0] <= time.monotonic():
        return False
    await handle_turn(client, entry[1])
    return True
