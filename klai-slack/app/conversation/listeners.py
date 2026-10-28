"""Bolt listeners for the conversation surfaces (SPEC-SLACK-001 §9).

The app runs with ``process_before_response=False``: Bolt acks an event before
the listener starts, and every action and view listener below acks first, so
all work happens after the 3-second ack. Every value that comes back from
Slack (option values, button values, private_metadata) is validated against
server-side state before it changes anything.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from slack_bolt.async_app import AsyncAck, AsyncApp
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app import db, repo
from app.clients.partner import KnowledgeBase, PartnerError
from app.config import get_settings
from app.conversation import turn
from app.conversation.i18n import t
from app.conversation.render import (
    ACTION_FEEDBACK,
    ACTION_PICK,
    ACTION_RETRY,
    ACTION_SOURCES,
    scope_label,
    stored_sources,
)
from app.conversation.slack_io import ephemeral, open_then_update
from app.conversation.turn import TurnRequest, partner_error_text, strip_mention
from app.conversation.views import (
    CALLBACK_CHANNEL_KB,
    CALLBACK_FEEDBACK,
    CALLBACK_THREAD_KB,
    View,
    feedback_text,
    feedback_view,
    home_view,
    kb_view,
    message_view,
    selected_values,
    sources_view,
    suggested_prompts,
)
from app.services import allowed_kbs as layer1
from app.services.scope import channel_scope
from app.services.slack_users import is_workspace_admin, user_lang

logger = structlog.get_logger()

Listener = Callable[..., Awaitable[None]]


def _metadata(body: dict[str, Any]) -> dict[str, str]:
    try:
        meta = json.loads(body.get("view", {}).get("private_metadata") or "{}")
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def _thread_of(body: dict[str, Any]) -> tuple[str, str]:
    """(channel, thread_ts) of the message whose button was clicked."""
    container = body.get("container") or {}
    message = body.get("message") or {}
    channel = (body.get("channel") or {}).get("id") or container.get("channel_id")
    thread_ts = message.get("thread_ts") or container.get("thread_ts") or message.get("ts")
    return str(channel), str(thread_ts)


def visible_failure(listener: Listener) -> Listener:
    """Log an unexpected exception and tell the user, instead of failing in silence."""

    @functools.wraps(listener)
    async def wrapper(**kwargs: Any) -> None:
        try:
            await listener(**kwargs)
        except Exception:
            logger.exception("slack_listener_failed", listener=listener.__name__)
            body: dict[str, Any] = kwargs["body"]
            client: AsyncWebClient = kwargs["client"]
            user_id = (body.get("user") or {}).get("id")
            channel = (
                _metadata(body).get("channel")
                or (body.get("channel") or {}).get("id")
                or (body.get("container") or {}).get("channel_id")
            )
            if user_id and channel:
                lang = await user_lang(client, user_id)
                await ephemeral(client, channel, user_id, t(lang, "unexpected"))

    return wrapper


def parse_kb_ids(values: list[str], allowed: set[int]) -> list[int] | None:
    """The selected ids in order, or None when any value is not an allowed id."""
    ids: list[int] = []
    for value in values:
        try:
            kb_id = int(value)
        except ValueError:
            return None
        if kb_id not in allowed:
            return None
        if kb_id not in ids:
            ids.append(kb_id)
    return ids


# --- messages ---------------------------------------------------------------


async def on_app_mention(
    body: dict[str, Any], event: dict[str, Any], client: AsyncWebClient, context: dict[str, Any]
) -> None:
    if event.get("bot_id") or event.get("subtype"):
        return
    bot_user_id = context.get("bot_user_id")
    await turn.handle_turn(
        client,
        TurnRequest(
            team_id=body["team_id"],
            channel_id=event["channel"],
            thread_ts=event.get("thread_ts") or event["ts"],
            message_ts=event["ts"],
            user_id=event["user"],
            text=strip_mention(event.get("text") or "", bot_user_id),
            is_dm=False,
            bot_user_id=bot_user_id,
        ),
    )


async def on_message(
    body: dict[str, Any], event: dict[str, Any], client: AsyncWebClient, context: dict[str, Any]
) -> None:
    """``message.im`` only; edits, deletes, joins and bot messages all carry a subtype or bot_id."""
    if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
        return
    bot_user_id = context.get("bot_user_id")
    await turn.handle_turn(
        client,
        TurnRequest(
            team_id=body["team_id"],
            channel_id=event["channel"],
            thread_ts=event.get("thread_ts") or event["ts"],
            message_ts=event["ts"],
            user_id=event["user"],
            text=strip_mention(event.get("text") or "", bot_user_id),
            is_dm=True,
            bot_user_id=bot_user_id,
        ),
    )


# --- App Home ---------------------------------------------------------------


async def build_home(lang: str, team_id: str) -> View:
    portal_url = f"{get_settings().portal_public_url.rstrip('/')}/admin/slack"
    try:
        workspace = await layer1.get_workspace(team_id)
    except layer1.NotLinked:
        return home_view(lang, portal_url=portal_url, org_name=None, kbs=None, channel_defaults={})
    kbs: list[KnowledgeBase] | None
    try:
        kbs = await layer1.allowed_kbs(team_id, workspace)
    except PartnerError:
        logger.warning("slack_home_kbs_failed", team_id=team_id, exc_info=True)
        kbs = None
    async with db.session() as s:
        defaults = await repo.list_channel_defaults(s, team_id)
    return home_view(
        lang,
        portal_url=portal_url,
        org_name=workspace.org_name,
        kbs=kbs,
        channel_defaults=defaults,
    )


async def on_app_home_opened(
    body: dict[str, Any], event: dict[str, Any], client: AsyncWebClient
) -> None:
    user_id = event["user"]
    lang = await user_lang(client, user_id)
    try:
        if event.get("tab") == "messages":
            # No thread_ts: for an agent app that makes the call fail silently (Slack docs).
            await client.assistant_threads_setSuggestedPrompts(
                channel_id=event["channel"], prompts=suggested_prompts(lang)
            )
        elif event.get("tab") == "home":
            await client.views_publish(
                user_id=user_id, view=await build_home(lang, body["team_id"])
            )
    except SlackApiError:
        logger.exception("slack_app_home_failed", tab=event.get("tab"))


# --- block actions ----------------------------------------------------------


async def build_thread_picker(lang: str, team_id: str, channel_id: str, thread_ts: str) -> View:
    try:
        kbs, effective = await channel_scope(team_id, channel_id)
    except layer1.NotLinked:
        return message_view(lang, t(lang, "not_linked"))
    except PartnerError as exc:
        return message_view(lang, partner_error_text(exc, lang))
    options = kbs if effective is None else [kb for kb in kbs if kb.id in effective]
    if not kbs:
        return message_view(lang, t(lang, "no_kbs"))
    if not options:
        return message_view(lang, t(lang, "empty_scope"))
    async with db.session() as s:
        current = await repo.get_thread_scope(s, team_id, channel_id, thread_ts)
    return kb_view(
        lang,
        callback_id=CALLBACK_THREAD_KB,
        kbs=options,
        initial_ids=current,
        metadata={"channel": channel_id, "thread_ts": thread_ts},
    )


@visible_failure
async def on_thread_kb_pick(ack: AsyncAck, body: dict[str, Any], client: AsyncWebClient) -> None:
    await ack()
    channel_id, thread_ts = _thread_of(body)
    lang = await user_lang(client, body["user"]["id"])
    await open_then_update(
        client,
        body["trigger_id"],
        lang,
        lambda: build_thread_picker(lang, body["team"]["id"], channel_id, thread_ts),
    )


@visible_failure
async def on_sources_all(
    ack: AsyncAck, body: dict[str, Any], action: dict[str, Any], client: AsyncWebClient
) -> None:
    await ack()
    lang = await user_lang(client, body["user"]["id"])
    sources = stored_sources(str(action.get("value") or ""))
    await client.views_open(trigger_id=body["trigger_id"], view=sources_view(lang, sources))


async def send_feedback(
    team_id: str,
    channel_id: str,
    thread_ts: str,
    completion_id: str,
    rating: str,
    text: str | None = None,
) -> bool:
    try:
        workspace = await layer1.get_workspace(team_id)
        await workspace.partner.feedback(
            message_id=completion_id,
            conversation_id=f"slack:{team_id}:{channel_id}:{thread_ts}",
            rating="thumbsUp" if rating == "up" else "thumbsDown",
            text=text,
        )
    except (layer1.NotLinked, PartnerError):
        logger.warning("slack_feedback_failed", team_id=team_id, exc_info=True)
        return False
    logger.info("slack_feedback_sent", completion_id=completion_id, rating=rating)
    return True


@visible_failure
async def on_answer_feedback(
    ack: AsyncAck, body: dict[str, Any], action: dict[str, Any], client: AsyncWebClient
) -> None:
    await ack()
    user_id = body["user"]["id"]
    team_id = body["team"]["id"]
    channel_id, thread_ts = _thread_of(body)
    lang = await user_lang(client, user_id)
    rating, _, completion_id = str(action.get("value") or "").partition(":")
    if rating not in ("up", "down") or not completion_id:
        logger.warning("slack_feedback_value_invalid")
        await ephemeral(client, channel_id, user_id, t(lang, "feedback_failed"), thread_ts)
        return
    if rating == "down":
        metadata = {"completion_id": completion_id, "channel": channel_id, "thread_ts": thread_ts}
        await client.views_open(trigger_id=body["trigger_id"], view=feedback_view(lang, metadata))
        return
    sent = await send_feedback(team_id, channel_id, thread_ts, completion_id, "up")
    reply = t(lang, "feedback_thanks" if sent else "feedback_failed")
    await ephemeral(client, channel_id, user_id, reply, thread_ts)


@visible_failure
async def on_retry_turn(
    ack: AsyncAck, body: dict[str, Any], action: dict[str, Any], client: AsyncWebClient
) -> None:
    await ack()
    if await turn.retry(client, body["team"]["id"], str(action.get("value") or "")):
        return
    user_id = body["user"]["id"]
    channel_id, thread_ts = _thread_of(body)
    lang = await user_lang(client, user_id)
    await ephemeral(client, channel_id, user_id, t(lang, "retry_expired"), thread_ts)


# --- view submissions -------------------------------------------------------


@visible_failure
async def on_channel_kb(
    ack: AsyncAck, body: dict[str, Any], view: dict[str, Any], client: AsyncWebClient
) -> None:
    await ack()
    team_id = body["team"]["id"]
    user_id = body["user"]["id"]
    channel_id = _metadata(body).get("channel", "")
    lang = await user_lang(client, user_id)

    async def say(text: str) -> None:
        await ephemeral(client, channel_id, user_id, text)

    try:
        admin = await is_workspace_admin(client, user_id)
    except SlackApiError:
        logger.exception("slack_kb_users_info_failed")
        await say(t(lang, "slack_error"))
        return
    if not admin:
        logger.warning("slack_kb_submit_not_admin", slack_user_id=user_id)
        await say(t(lang, "kb_admin_only"))
        return
    try:
        kbs = await layer1.allowed_kbs(team_id)
    except layer1.NotLinked:
        await say(t(lang, "not_linked"))
        return
    except PartnerError as exc:
        await say(partner_error_text(exc, lang))
        return
    kb_ids = parse_kb_ids(selected_values(view), {kb.id for kb in kbs})
    if kb_ids is None:
        logger.warning("slack_kb_submit_rejected", scope="channel", channel_id=channel_id)
        await say(t(lang, "invalid_selection"))
        return
    async with db.session() as s:
        await repo.set_channel_default(s, team_id, channel_id, kb_ids or None, user_id)
    logger.info("slack_channel_default_set", channel_id=channel_id, kb_count=len(kb_ids))
    names = [kb.name for kb in kbs if kb.id in kb_ids] or None
    await say(t(lang, "saved_channel", names=scope_label(lang, names)))


@visible_failure
async def on_thread_kb(
    ack: AsyncAck, body: dict[str, Any], view: dict[str, Any], client: AsyncWebClient
) -> None:
    await ack()
    team_id = body["team"]["id"]
    user_id = body["user"]["id"]
    meta = _metadata(body)
    channel_id, thread_ts = meta.get("channel", ""), meta.get("thread_ts", "")
    lang = await user_lang(client, user_id)

    async def say(text: str) -> None:
        await ephemeral(client, channel_id, user_id, text, thread_ts)

    try:
        kbs, effective = await channel_scope(team_id, channel_id)
    except layer1.NotLinked:
        await say(t(lang, "not_linked"))
        return
    except PartnerError as exc:
        await say(partner_error_text(exc, lang))
        return
    options = kbs if effective is None else [kb for kb in kbs if kb.id in effective]
    kb_ids = parse_kb_ids(selected_values(view), {kb.id for kb in options})
    if kb_ids is None or not thread_ts:
        logger.warning("slack_kb_submit_rejected", scope="thread", channel_id=channel_id)
        await say(t(lang, "invalid_selection"))
        return
    async with db.session() as s:
        await repo.set_thread_scope(s, team_id, channel_id, thread_ts, kb_ids or None, user_id)
    logger.info("slack_thread_scope_set", channel_id=channel_id, kb_count=len(kb_ids))
    chosen = kb_ids or (None if effective is None else effective)
    names = None if chosen is None else [kb.name for kb in kbs if kb.id in chosen]
    await say(t(lang, "saved_thread", names=scope_label(lang, names)))


@visible_failure
async def on_feedback_reason(
    ack: AsyncAck, body: dict[str, Any], view: dict[str, Any], client: AsyncWebClient
) -> None:
    await ack()
    meta = _metadata(body)
    user_id = body["user"]["id"]
    channel_id, thread_ts = meta.get("channel", ""), meta.get("thread_ts", "")
    lang = await user_lang(client, user_id)
    sent = await send_feedback(
        body["team"]["id"],
        channel_id,
        thread_ts,
        meta.get("completion_id", ""),
        "down",
        feedback_text(view) or None,
    )
    reply = t(lang, "feedback_thanks" if sent else "feedback_failed")
    await ephemeral(client, channel_id, user_id, reply, thread_ts)


def register(app: AsyncApp) -> None:
    app.event("app_mention")(on_app_mention)
    app.event("message")(on_message)
    app.event("app_home_opened")(on_app_home_opened)
    app.action(ACTION_PICK)(on_thread_kb_pick)
    app.action(ACTION_SOURCES)(on_sources_all)
    app.action(ACTION_FEEDBACK)(on_answer_feedback)
    app.action(ACTION_RETRY)(on_retry_turn)
    app.view(CALLBACK_CHANNEL_KB)(on_channel_kb)
    app.view(CALLBACK_THREAD_KB)(on_thread_kb)
    app.view(CALLBACK_FEEDBACK)(on_feedback_reason)
