"""Modals and the App Home (SPEC-SLACK-001 §9).

Block Kit limits that apply here: a view holds at most 100 blocks, a static
select at most 100 options with option text of at most 75 characters, a
section at most 3000 characters, and a view title at most 24 characters (all
titles below are shorter).
"""

from __future__ import annotations

import json
from typing import Any

from app.clients.partner import KnowledgeBase, Source
from app.conversation.i18n import t
from app.conversation.render import MAX_SECTION, escape, source_lines, truncate

MAX_OPTIONS = 100
MAX_OPTION_TEXT = 75
MAX_VIEW_BLOCKS = 100

CALLBACK_CHANNEL_KB = "channel_kb"
CALLBACK_THREAD_KB = "thread_kb"
CALLBACK_FEEDBACK = "feedback_reason"
KB_BLOCK = "kbs"
KB_ACTION = "kb_ids"
REASON_BLOCK = "reason"
REASON_ACTION = "reason"
TEXT_BLOCK = "text"
TEXT_ACTION = "text"
FEEDBACK_REASONS = ("incorrect", "incomplete", "irrelevant", "sources")

View = dict[str, Any]


def _plain(text: str) -> dict[str, Any]:
    return {"type": "plain_text", "text": text}


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": truncate(text, MAX_SECTION)}}


def message_view(lang: str, text: str, title_key: str = "kb_title") -> View:
    return {
        "type": "modal",
        "title": _plain(t(lang, title_key)),
        "close": _plain(t(lang, "close")),
        "blocks": [_section(text)],
    }


def loading_view(lang: str) -> View:
    return message_view(lang, t(lang, "loading"))


def _option(kb: KnowledgeBase) -> dict[str, Any]:
    return {"text": _plain(truncate(kb.name, MAX_OPTION_TEXT)), "value": str(kb.id)}


def kb_view(
    lang: str,
    *,
    callback_id: str,
    kbs: list[KnowledgeBase],
    initial_ids: list[int] | None,
    metadata: dict[str, str],
) -> View:
    """The multi-select of ``/klai kb`` (``channel_kb``) and the thread picker (``thread_kb``)."""
    thread = callback_id == CALLBACK_THREAD_KB
    shown = kbs[:MAX_OPTIONS]
    element: dict[str, Any] = {
        "type": "multi_static_select",
        "action_id": KB_ACTION,
        "placeholder": _plain(t(lang, "kb_placeholder")),
        "options": [_option(kb) for kb in shown],
    }
    initial = [_option(kb) for kb in shown if initial_ids and kb.id in initial_ids]
    if initial:
        element["initial_options"] = initial
    blocks: list[dict[str, Any]] = [
        {
            "type": "input",
            "block_id": KB_BLOCK,
            "optional": True,
            "label": _plain(t(lang, "kb_label_thread" if thread else "kb_label_channel")),
            "hint": _plain(t(lang, "kb_hint_thread" if thread else "kb_hint_channel")),
            "element": element,
        }
    ]
    if len(kbs) > MAX_OPTIONS:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": t(lang, "kb_more", n=MAX_OPTIONS)}],
            }
        )
    return {
        "type": "modal",
        "callback_id": callback_id,
        "private_metadata": json.dumps(metadata),
        "title": _plain(t(lang, "kb_title")),
        "submit": _plain(t(lang, "save")),
        "close": _plain(t(lang, "cancel")),
        "blocks": blocks,
    }


def selected_values(view: dict[str, Any]) -> list[str]:
    """Raw option values of a submitted KB view; the caller validates every one."""
    state = view.get("state", {}).get("values", {}).get(KB_BLOCK, {}).get(KB_ACTION, {})
    return [str(option.get("value")) for option in state.get("selected_options") or []]


def sources_view(lang: str, sources: list[Source] | None) -> View:
    if not sources:
        return message_view(lang, t(lang, "sources_expired"), "sources_title")
    view = message_view(lang, "", "sources_title")
    view["blocks"] = [_section(line) for line in source_lines(sources)[:MAX_VIEW_BLOCKS]]
    return view


def feedback_view(lang: str, metadata: dict[str, str]) -> View:
    return {
        "type": "modal",
        "callback_id": CALLBACK_FEEDBACK,
        "private_metadata": json.dumps(metadata),
        "title": _plain(t(lang, "feedback_title")),
        "submit": _plain(t(lang, "feedback_send")),
        "close": _plain(t(lang, "cancel")),
        "blocks": [
            {
                "type": "input",
                "block_id": REASON_BLOCK,
                "label": _plain(t(lang, "feedback_reason")),
                "element": {
                    "type": "radio_buttons",
                    "action_id": REASON_ACTION,
                    "options": [
                        {"text": _plain(t(lang, f"feedback_{reason}")), "value": reason}
                        for reason in FEEDBACK_REASONS
                    ],
                },
            },
            {
                "type": "input",
                "block_id": TEXT_BLOCK,
                "optional": True,
                "label": _plain(t(lang, "feedback_text")),
                "element": {
                    "type": "plain_text_input",
                    "action_id": TEXT_ACTION,
                    "multiline": True,
                    "max_length": 1000,
                },
            },
        ],
    }


def feedback_text(view: dict[str, Any]) -> str:
    """ "<reason>: <text>" from a submitted feedback view; an unknown reason is dropped."""
    values = view.get("state", {}).get("values", {})
    selected = values.get(REASON_BLOCK, {}).get(REASON_ACTION, {}).get("selected_option") or {}
    reason = selected.get("value")
    text = (values.get(TEXT_BLOCK, {}).get(TEXT_ACTION, {}).get("value") or "").strip()
    parts = [reason if reason in FEEDBACK_REASONS else None, text or None]
    return ": ".join(part for part in parts if part)


def home_view(
    lang: str,
    *,
    portal_url: str,
    org_name: str | None,
    kbs: list[KnowledgeBase] | None,
    channel_defaults: dict[str, list[int]],
) -> View:
    """``org_name=None`` = not linked; ``kbs=None`` = linked, but the KB list failed to load."""
    blocks: list[dict[str, Any]] = [{"type": "header", "text": _plain("Klai")}]
    if org_name is None:
        blocks.append(_section(t(lang, "home_not_linked")))
        blocks.append(_section(t(lang, "home_connect_hint", portal_url=portal_url)))
    else:
        blocks.append(_section(t(lang, "home_connected", org=escape(org_name))))
        blocks.append({"type": "divider"})
        if kbs is None:
            kb_text = t(lang, "home_kbs_error")
        elif not kbs:
            kb_text = t(lang, "home_kbs_none")
        else:
            kb_text = "\n".join(f"• {escape(kb.name)}" for kb in kbs)
        blocks.append(_section(f"{t(lang, 'home_kbs')}\n{kb_text}"))
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": t(lang, "home_kbs_manage", portal_url=portal_url)}
                ],
            }
        )
        blocks.append({"type": "divider"})
        lines = [
            _channel_line(lang, channel, ids, kbs) for channel, ids in channel_defaults.items()
        ]
        channel_text = "\n".join(lines) or t(lang, "home_channels_none")
        blocks.append(_section(f"{t(lang, 'home_channels')}\n{channel_text}"))
    blocks.append({"type": "divider"})
    blocks.append(_section(t(lang, "home_help")))
    blocks.append(
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t(lang, "home_disclaimer")}]}
    )
    return {"type": "home", "blocks": blocks}


def _channel_line(
    lang: str, channel_id: str, kb_ids: list[int], kbs: list[KnowledgeBase] | None
) -> str:
    if kbs is None:  # names unknown while the KB list cannot be loaded
        return f"<#{channel_id}>"
    names = {kb.id: kb.name for kb in kbs}
    current = [f"*{escape(names[i])}*" for i in kb_ids if i in names]
    return f"<#{channel_id}>: {', '.join(current) or t(lang, 'home_channel_revoked')}"


def suggested_prompts(lang: str) -> list[dict[str, str]]:
    return [
        {"title": t(lang, f"prompt_{n}_title"), "message": t(lang, f"prompt_{n}_message")}
        for n in range(1, 5)
    ]
