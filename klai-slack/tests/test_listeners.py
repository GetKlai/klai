"""`/klai kb`, `/klai help`, block actions, view submissions and the App Home."""

from __future__ import annotations

import json
from typing import Any

from app.clients.partner import KnowledgeBase, Source
from app.commands import SUBCOMMANDS, lookup
from app.commands.context import CommandContext
from app.conversation import listeners
from app.conversation.render import remember_sources
from app.conversation.views import MAX_VIEW_BLOCKS, home_view
from tests.fakes import FakeSlack, Partner, Store

COMMAND = {
    "team_id": "T1",
    "channel_id": "C1",
    "user_id": "UADMIN",
    "trigger_id": "trig-1",
    "api_app_id": "A1",
}
MESSAGE_BODY = {
    "team": {"id": "T1"},
    "user": {"id": "U1"},
    "trigger_id": "trig-2",
    "channel": {"id": "C1"},
    "container": {"type": "message", "channel_id": "C1"},
    "message": {"ts": "900.2", "thread_ts": "900.1"},
}


async def _ack(**_kwargs: Any) -> None:
    return None


def _ctx(slack: FakeSlack, text: str, said: list[str]) -> CommandContext:
    async def respond(text: str, response_type: str) -> None:
        assert response_type == "ephemeral"
        said.append(text)

    return CommandContext(
        args="",
        command={**COMMAND, "text": text},
        client=slack,  # pyright: ignore[reportArgumentType]
        respond=respond,  # pyright: ignore[reportArgumentType]
    )


def _selected(*values: str) -> dict[str, Any]:
    options = [{"value": v} for v in values]
    return {"kbs": {"kb_ids": {"selected_options": options}}}


async def _submit(listener: Any, slack: FakeSlack, metadata: dict[str, str], *values: str) -> None:
    view = {"private_metadata": json.dumps(metadata), "state": {"values": _selected(*values)}}
    body = {"team": {"id": "T1"}, "user": {"id": "UADMIN"}, "view": view}
    await listener(ack=_ack, body=body, view=view, client=slack)


async def test_kb_command_opens_loading_view_then_the_select(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    slack.users["UADMIN"] = {"is_admin": True}
    partner.kbs = [
        {"id": i, "name": f"Kennisbank {i} " + "x" * 80, "slug": f"kb{i}", "access_level": "read"}
        for i in range(1, 121)
    ]
    store.channel_defaults[("T1", "C1")] = [2, 999]
    await SUBCOMMANDS["kb"].run(_ctx(slack, "kb", []))

    (opened,) = slack.named("views_open")
    assert opened["trigger_id"] == "trig-1"
    assert "laden" in opened["view"]["blocks"][0]["text"]["text"]
    (updated,) = slack.named("views_update")
    view = updated["view"]
    assert updated["view_id"] == "V1"
    assert view["callback_id"] == "channel_kb"
    assert json.loads(view["private_metadata"]) == {"channel": "C1"}
    element = view["blocks"][0]["element"]
    assert element["type"] == "multi_static_select"
    assert len(element["options"]) == 100
    assert all(len(o["text"]["text"]) <= 75 for o in element["options"])
    assert [o["value"] for o in element["initial_options"]] == ["2"]
    assert view["blocks"][0]["hint"]["text"] == "Leeg laten = alle vrijgegeven kennisbanken"


async def test_kb_command_refuses_non_admins(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    await SUBCOMMANDS["kb"].run(_ctx(slack, "kb", []))
    view = slack.named("views_update")[0]["view"]
    assert "callback_id" not in view
    assert "Alleen beheerders" in view["blocks"][0]["text"]["text"]


async def test_channel_kb_submission_rejects_an_id_outside_layer1(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    slack.users["UADMIN"] = {"is_admin": True}
    for values in (("2", "99"), ("2", "abc")):
        await _submit(listeners.on_channel_kb, slack, {"channel": "C1"}, *values)
    assert store.channel_defaults == {}
    assert all("niets" in c["text"] for c in slack.named("chat_postEphemeral"))


async def test_channel_kb_submission_requires_an_admin_again(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    await _submit(listeners.on_channel_kb, slack, {"channel": "C1"}, "2")
    assert store.channel_defaults == {}


async def test_channel_kb_empty_submission_resets_to_all_allowed(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    slack.users["UADMIN"] = {"is_admin": True}
    store.channel_defaults[("T1", "C1")] = [2]
    await _submit(listeners.on_channel_kb, slack, {"channel": "C1"})
    assert store.channel_defaults == {}
    assert "alle vrijgegeven kennisbanken" in slack.named("chat_postEphemeral")[0]["text"]


async def test_thread_picker_offers_only_the_channel_set_and_saves_a_subset(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    store.channel_defaults[("T1", "C1")] = [2, 3]
    await listeners.on_thread_kb_pick(ack=_ack, body=MESSAGE_BODY, client=slack)
    view = slack.named("views_update")[0]["view"]
    assert view["callback_id"] == "thread_kb"
    assert [o["value"] for o in view["blocks"][0]["element"]["options"]] == ["2", "3"]
    metadata = json.loads(view["private_metadata"])
    assert metadata == {"channel": "C1", "thread_ts": "900.1"}

    await _submit(listeners.on_thread_kb, slack, metadata, "3")
    assert store.thread_scopes == {("T1", "C1", "900.1"): [3]}


async def test_feedback_up_posts_thumbs_up_for_the_completion(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    await listeners.on_answer_feedback(
        ack=_ack, body=MESSAGE_BODY, action={"value": "up:cmpl-7"}, client=slack
    )
    assert partner.bodies("/partner/v1/feedback") == [
        {"message_id": "cmpl-7", "conversation_id": "slack:T1:C1:900.1", "rating": "thumbsUp"}
    ]
    assert slack.named("chat_postEphemeral")[0]["text"] == "Bedankt voor je feedback."


async def test_feedback_down_asks_a_reason_then_posts_thumbs_down_with_text(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    await listeners.on_answer_feedback(
        ack=_ack, body=MESSAGE_BODY, action={"value": "down:cmpl-7"}, client=slack
    )
    assert partner.bodies("/partner/v1/feedback") == []
    view = slack.named("views_open")[0]["view"]
    assert view["callback_id"] == "feedback_reason"

    view["state"] = {
        "values": {
            "reason": {"reason": {"selected_option": {"value": "incomplete"}}},
            "text": {"text": {"value": " Mist de uitzonderingen. "}},
        }
    }
    body = {"team": {"id": "T1"}, "user": {"id": "U1"}, "view": view}
    await listeners.on_feedback_reason(ack=_ack, body=body, view=view, client=slack)
    assert partner.bodies("/partner/v1/feedback") == [
        {
            "message_id": "cmpl-7",
            "conversation_id": "slack:T1:C1:900.1",
            "rating": "thumbsDown",
            "text": "incomplete: Mist de uitzonderingen.",
        }
    ]


async def test_sources_all_lists_every_stored_source(slack: FakeSlack) -> None:
    sources = [Source(title=f"Doc {i}", url=f"https://docs.example.com/{i}") for i in range(5)]
    remember_sources("cmpl-8", sources)
    await listeners.on_sources_all(
        ack=_ack, body=MESSAGE_BODY, action={"value": "cmpl-8"}, client=slack
    )
    assert len(slack.named("views_open")[0]["view"]["blocks"]) == 5

    await listeners.on_sources_all(
        ack=_ack, body=MESSAGE_BODY, action={"value": "cmpl-unknown"}, client=slack
    )
    assert "niet meer beschikbaar" in str(slack.named("views_open")[1]["view"])


async def test_help_and_unknown_subcommands_explain_the_channel_scope(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    store.channel_defaults[("T1", "C1")] = [1]
    said: list[str] = []
    for text in ("help", "iets anders"):
        subcommand, _ = lookup(text)
        await subcommand.run(_ctx(slack, text, said))
    assert len(said) == 2 and said[0] == said[1]
    assert "*In dit kanaal:* *Handboek*" in said[0]
    assert "slack://app?team=T1&id=A1&tab=home" in said[0]


async def test_messages_tab_explains_scope_and_switching_above_four_prompts(
    slack: FakeSlack,
) -> None:
    event = {"type": "app_home_opened", "user": "U1", "channel": "D1", "tab": "messages"}
    await listeners.on_app_home_opened(body={"team_id": "T1"}, event=event, client=slack)  # pyright: ignore[reportArgumentType]
    (call,) = slack.named("assistant_threads_setSuggestedPrompts")
    assert call["channel_id"] == "D1"
    assert "thread_ts" not in call
    assert len(call["prompts"]) == 4
    assert "alle kennisbanken" in call["title"]
    assert "Kennisbanken kiezen" in call["title"]


async def test_home_tab_publishes_status_kbs_and_channel_defaults(
    store: Store, partner: Partner, slack: FakeSlack
) -> None:
    store.channel_defaults[("T1", "C1")] = [2]
    event = {"type": "app_home_opened", "user": "U1", "channel": "D1", "tab": "home"}
    await listeners.on_app_home_opened(body={"team_id": "T1"}, event=event, client=slack)  # pyright: ignore[reportArgumentType]
    text = json.dumps(slack.named("views_publish")[0]["view"])
    assert "Voorbeeld BV" in text
    assert "<#C1>: *Support*" in text
    assert "https://my.getklai.com/admin/slack" in text


async def test_home_tab_unlinked_points_to_the_portal(store: Store, slack: FakeSlack) -> None:
    store.link = None
    event = {"type": "app_home_opened", "user": "U1", "channel": "D1", "tab": "home"}
    await listeners.on_app_home_opened(body={"team_id": "T1"}, event=event, client=slack)  # pyright: ignore[reportArgumentType]
    text = json.dumps(slack.named("views_publish")[0]["view"], ensure_ascii=False)
    assert "Nog niet gekoppeld" in text
    assert "Koppel Klai vanuit de <https://my.getklai.com/admin/slack|Klai-portal>" in text
    assert "(Beheer → Slack)" in text
    assert "/klai connect" not in text


async def test_help_when_unlinked_points_to_the_portal(store: Store, slack: FakeSlack) -> None:
    store.link = None
    said: list[str] = []
    await SUBCOMMANDS["help"].run(_ctx(slack, "help", said))
    assert "Klai-portal (Beheer → Slack)" in said[0]
    assert "/klai connect" not in said[0]


def test_home_view_stays_within_block_kit_limits() -> None:
    kbs = [
        KnowledgeBase(id=i, name="k" * 120, slug=f"k{i}", access_level="read") for i in range(300)
    ]
    defaults = {f"C{i:04d}": [i] for i in range(300)}
    view = home_view(
        "en",
        portal_url="https://portal.example.com",
        org_name="Org",
        kbs=kbs,
        channel_defaults=defaults,
    )
    assert len(view["blocks"]) <= MAX_VIEW_BLOCKS
    sections = [b["text"]["text"] for b in view["blocks"] if b["type"] == "section"]
    assert all(len(s) <= 3000 for s in sections)
