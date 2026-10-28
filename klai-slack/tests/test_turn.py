"""Turns end to end: listener → scope → Partner API (MockTransport) → Slack (fake client)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from slack_bolt.request.async_request import AsyncBoltRequest

from app.conversation import listeners, sessions
from app.services import allowed_kbs
from app.slack_app import dedupe_events
from tests.fakes import FakeSlack, Partner, Store, completion

CONTEXT = {"bot_user_id": "UBOT"}
CHAT = "/partner/v1/chat/completions"


def _dm(text: str = "Hoeveel verlof heb ik?", ts: str = "100.1", thread_ts: str | None = None):
    event = {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1"}
    event.update({"text": text, "ts": ts})
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {"team_id": "T1", "event": event}, event


def _mention(text: str, ts: str = "200.1", thread_ts: str | None = None, channel: str = "C1"):
    event = {"type": "app_mention", "channel": channel, "user": "U1", "text": text, "ts": ts}
    if thread_ts:
        event["thread_ts"] = thread_ts
    return {"team_id": "T1", "event_id": f"Ev{ts}", "type": "event_callback", "event": event}, event


async def _send_dm(slack: FakeSlack, **kwargs: Any) -> None:
    body, event = _dm(**kwargs)
    await listeners.on_message(body=body, event=event, client=slack, context=CONTEXT)  # pyright: ignore[reportArgumentType]


async def _send_mention(slack: FakeSlack, text: str, **kwargs: Any) -> None:
    body, event = _mention(text, **kwargs)
    await listeners.on_app_mention(body=body, event=event, client=slack, context=CONTEXT)  # pyright: ignore[reportArgumentType]


def _answers(slack: FakeSlack) -> list[dict[str, Any]]:
    return [
        kwargs
        for name, kwargs in slack.calls
        if name in ("chat_postMessage", "chat_update") and kwargs.get("blocks")
    ]


def _searched_in(message: dict[str, Any]) -> str:
    return next(b for b in message["blocks"] if b["type"] == "context")["elements"][0]["text"]


async def test_dm_answer_searches_all_allowed_and_shows_the_full_answer(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    """Spec §12 test 3."""
    partner.chat_responses.append(httpx.Response(200, json=completion(sources=5)))
    await _send_dm(slack)

    (chat,) = partner.chats()
    assert "knowledge_base_ids" not in chat["knowledge"]
    assert chat["messages"] == [{"role": "user", "content": "Hoeveel verlof heb ik?"}]

    (answer,) = _answers(slack)
    assert answer["thread_ts"] == "100.1"
    assert answer["unfurl_links"] is False and answer["unfurl_media"] is False
    assert "reply_broadcast" not in answer
    assert _searched_in(answer) == "Gezocht in: *Handboek*, *Support*, *Productinfo*"
    section = next(b for b in answer["blocks"] if b["type"] == "section")
    assert section["text"]["text"].count("\n") == 2  # three sources
    assert answer["blocks"][-1]["type"] == "context_actions"

    # Agent session around the turn, titled after the first answer.
    statuses = [c["status"] for c in slack.named("agents_sessions_setStatus")]
    assert statuses == ["processing", "active"]
    assert slack.named("agents_sessions_rename") == [
        {"channel_id": "D1", "thread_ts": "100.1", "title": "Hoeveel verlof heb ik?"}
    ]
    assert [(t.role, t.completion_id, t.kb_ids) for t in store.turns] == [
        ("user", None, [1, 2, 3]),
        ("assistant", "chatcmpl-synthetic-1", [1, 2, 3]),
    ]


async def test_channel_default_scopes_a_mention_and_thread_picker_cannot_widen(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    """Spec §12 test 4."""
    slack.users["UADMIN"] = {"is_admin": True}
    channel_view = {
        "private_metadata": '{"channel": "C1"}',
        "state": {"values": {"kbs": {"kb_ids": {"selected_options": [{"value": "2"}]}}}},
    }
    await _ack_view(listeners.on_channel_kb, slack, channel_view, user="UADMIN")
    assert store.channel_defaults == {("T1", "C1"): [2]}

    await _send_mention(slack, "<@UBOT> Wat is de SLA?")
    assert partner.chats()[0]["knowledge"]["knowledge_base_ids"] == [2]
    assert "*Support*" in _searched_in(_answers(slack)[0])

    thread_view = {
        "private_metadata": '{"channel": "C1", "thread_ts": "200.1"}',
        "state": {"values": {"kbs": {"kb_ids": {"selected_options": [{"value": "1"}]}}}},
    }
    await _ack_view(listeners.on_thread_kb, slack, thread_view)
    assert store.thread_scopes == {}
    assert "niet (meer) beschikbaar" in slack.named("chat_postEphemeral")[-1]["text"]


async def test_revoked_kb_drops_out_and_an_emptied_default_blocks_the_call(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    """Spec §12 test 5."""
    store.channel_defaults[("T1", "C1")] = [2, 3]
    partner.kbs = [kb for kb in partner.kbs if kb["id"] != 2]  # portal removes KB 2

    await _send_mention(slack, "<@UBOT> vraag een", ts="300.1")
    assert partner.chats()[-1]["knowledge"]["knowledge_base_ids"] == [3]

    store.channel_defaults[("T1", "C1")] = [2]
    allowed_kbs.invalidate("T1")
    await _send_mention(slack, "<@UBOT> vraag twee", ts="301.1")
    assert len(partner.chats()) == 1, "no Partner API call on an empty intersection"
    notice = slack.named("chat_postMessage")[-1]
    assert "kies opnieuw" in notice["text"].lower()
    assert notice["blocks"][-1]["elements"][0]["action_id"] == "thread_kb_pick"


async def test_slack_retry_of_the_same_event_answers_once(
    monkeypatch: pytest.MonkeyPatch, store: Store, partner: Partner, slack: FakeSlack
) -> None:
    """Spec §12 test 9: the dedupe middleware stops the redelivery before the listener."""
    from app import repo

    seen: set[str] = set()

    async def mark(_s: object, event_id: str) -> bool:
        first = event_id not in seen
        seen.add(event_id)
        return first

    monkeypatch.setattr(repo, "mark_event_processed", mark)
    body, event = _mention("<@UBOT> Wat is de SLA?")

    async def next_() -> None:
        await listeners.on_app_mention(body=body, event=event, client=slack, context=CONTEXT)  # pyright: ignore[reportArgumentType]

    await dedupe_events(body, AsyncBoltRequest(body="{}", headers={}), next_)
    await dedupe_events(
        body, AsyncBoltRequest(body="{}", headers={"x-slack-retry-num": ["1"]}), next_
    )

    assert len(partner.chats()) == 1
    assert len(_answers(slack)) == 1


async def test_mention_replies_via_placeholder_in_the_thread(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    await _send_mention(slack, "<@UBOT> Wat is de SLA?", ts="400.2", thread_ts="400.1")
    placeholder = slack.named("chat_postMessage")[0]
    assert placeholder["text"] == "Ik zoek in 3 kennisbanken…"
    assert placeholder["thread_ts"] == "400.1"
    update = slack.named("chat_update")[0]
    assert update["ts"] == "1700000000.000001", "the answer replaces the placeholder"
    assert slack.named("agents_sessions_setStatus") == []


async def test_unseen_thread_is_seeded_once_from_conversations_replies(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    slack.replies = [
        {"ts": "500.1", "user": "U2", "text": "Wie weet hoe het verlof zit?"},
        {"ts": "500.2", "user": "U3", "text": "Geen idee"},
        {"ts": "500.3", "bot_id": "B1", "user": "UBOT", "text": "Eerder antwoord"},
        {"ts": "500.4", "user": "U1", "text": "<@UBOT> en voor parttimers?"},
    ]
    await _send_mention(slack, "<@UBOT> en voor parttimers?", ts="500.4", thread_ts="500.1")

    assert slack.named("conversations_replies") == [{"channel": "C1", "ts": "500.1", "limit": 15}]
    assert partner.chats()[0]["messages"] == [
        {"role": "user", "content": "Wie weet hoe het verlof zit?\n\nGeen idee"},
        {"role": "assistant", "content": "Eerder antwoord"},
        {"role": "user", "content": "en voor parttimers?"},
    ]

    await _send_mention(slack, "<@UBOT> en freelancers?", ts="500.5", thread_ts="500.1")
    assert len(slack.named("conversations_replies")) == 1, "stored turns replace the Slack read"
    assert [m["role"] for m in partner.chats()[1]["messages"]] == ["user", "assistant", "user"]


async def test_history_is_capped_at_twenty_turns(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    from types import SimpleNamespace

    for i in range(30):
        store.turns.append(
            SimpleNamespace(
                team_id="T1",
                channel_id="D1",
                thread_ts="600.1",
                role="user" if i % 2 == 0 else "assistant",
                text=f"beurt {i}",
            )
        )
    await _send_dm(slack, text="nog een vraag", ts="600.9", thread_ts="600.1")
    messages = partner.chats()[0]["messages"]
    assert len(messages) == 21
    assert messages[0]["content"] == "beurt 10"
    assert messages[-1] == {"role": "user", "content": "nog een vraag"}


async def test_rate_limit_shows_retry_after_and_the_retry_button_reruns(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    partner.chat_responses.append(httpx.Response(429, headers={"Retry-After": "30"}, json={}))
    await _send_mention(slack, "<@UBOT> vraag", ts="700.1")

    notice = slack.named("chat_update")[-1]
    assert "30 s" in notice["text"]
    button = notice["blocks"][-1]["elements"][0]
    assert (button["action_id"], button["value"]) == ("retry_turn", "C1:700.1")
    assert store.turns == []

    await listeners.on_retry_turn(
        ack=_noop_ack,
        body={"team": {"id": "T1"}, "user": {"id": "U9"}, "channel": {"id": "C1"}},
        action={"value": "C1:700.1"},
        client=slack,  # pyright: ignore[reportArgumentType]
    )
    assert len(partner.chats()) == 2
    assert len(store.turns) == 2


async def test_partner_5xx_shows_unavailable_with_request_id(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    partner.chat_responses.append(httpx.Response(503, json={}))
    await _send_mention(slack, "<@UBOT> vraag")
    text = slack.named("chat_update")[-1]["text"]
    request_id = partner.requests[-1].headers["X-Request-ID"]
    assert "niet bereikbaar" in text and request_id in text


async def test_permission_error_is_an_empty_scope_and_refreshes_layer1(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    body = {"detail": {"error": {"type": "permission_error", "message": "outside scope"}}}
    partner.chat_responses.append(httpx.Response(403, json=body))
    await _send_mention(slack, "<@UBOT> vraag")
    assert "kies opnieuw" in slack.named("chat_update")[-1]["text"].lower()
    assert "T1" not in allowed_kbs._cache


@pytest.mark.parametrize(
    ("setup", "expected"),
    [("unlinked", "nog niet gekoppeld"), ("no_kbs", "nog geen kennisbanken vrijgegeven")],
)
async def test_unlinked_or_empty_layer1_answers_visibly_without_chat(
    store: Store, partner: Partner, slack: FakeSlack, setup: str, expected: str
) -> None:
    if setup == "unlinked":
        store.link = None
    else:
        partner.kbs = []
    await _send_mention(slack, "<@UBOT> vraag")
    assert partner.chats() == []
    assert expected in slack.named("chat_postMessage")[-1]["text"].lower()


async def test_sessions_failure_is_remembered_per_team(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    slack.errors["agents_sessions_setStatus"] = "method_not_supported"
    await _send_dm(slack, ts="800.1")
    assert slack.named("chat_postMessage")[0]["text"] == "Ik zoek in 3 kennisbanken…"
    assert len(_answers(slack)) == 1
    assert "T1" in sessions._unsupported

    await _send_dm(slack, ts="800.2")
    assert len(slack.named("agents_sessions_setStatus")) == 1, "not retried for this team"
    assert len(_answers(slack)) == 2


async def test_bot_not_in_channel_stops_quietly(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    slack.errors["chat_postMessage"] = "not_in_channel"
    await _send_mention(slack, "<@UBOT> vraag")
    assert partner.chats() == []
    assert [name for name, _ in slack.calls if name.startswith("chat_")] == ["chat_postMessage"]


async def test_bot_and_edited_messages_are_ignored(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    for extra in ({"bot_id": "B1"}, {"subtype": "message_changed"}):
        body, event = _dm()
        event.update(extra)
        await listeners.on_message(body=body, event=event, client=slack, context=CONTEXT)  # pyright: ignore[reportArgumentType]
    assert slack.calls == []


async def _noop_ack(**_kwargs: Any) -> None:
    return None


async def _ack_view(
    listener: Any, slack: FakeSlack, view: dict[str, Any], user: str = "U1"
) -> None:
    body = {"team": {"id": "T1"}, "user": {"id": user}, "view": view}
    await listener(ack=_noop_ack, body=body, view=view, client=slack)
