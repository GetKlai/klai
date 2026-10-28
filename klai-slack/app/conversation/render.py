"""Answer messages in Block Kit (SPEC-SLACK-001 §9).

Blocks are plain dicts rather than slack_sdk models, so a test can compare the
exact payload Slack receives. Limits that shape this module, from the Block Kit
reference: all ``markdown`` blocks of one payload share 12 000 characters (so
a longer answer is split into follow-up messages), ``section`` text is capped
at 3000, and the ``text`` fallback is kept under 4000 so notifications and
screen readers get it whole.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.clients.partner import ChatResult, Source
from app.conversation.i18n import t

MAX_MARKDOWN = 12_000
MAX_TEXT = 4_000
MAX_SECTION = 3_000
TOP_SOURCES = 3
SOURCES_TTL_S = 3600.0

ACTION_PICK = "thread_kb_pick"
ACTION_SOURCES = "sources_all"
ACTION_FEEDBACK = "answer_feedback"
ACTION_RETRY = "retry_turn"

Block = dict[str, Any]


@dataclass(frozen=True)
class SlackMessage:
    text: str
    blocks: list[Block]


@dataclass(frozen=True)
class RenderedAnswer:
    messages: list[SlackMessage]


# completion_id -> (expires_at, deduplicated sources) for the "Alle n bronnen" modal.
_sources: dict[str, tuple[float, list[Source]]] = {}


def remember_sources(completion_id: str, sources: list[Source]) -> None:
    now = time.monotonic()
    for key in [k for k, (expires, _) in _sources.items() if expires <= now]:
        del _sources[key]
    _sources[completion_id] = (now + SOURCES_TTL_S, sources)


def stored_sources(completion_id: str) -> list[Source] | None:
    entry = _sources.get(completion_id)
    if entry is None or entry[0] <= time.monotonic():
        return None
    return entry[1]


def escape(text: str) -> str:
    """Slack mrkdwn control characters; see "Escaping text" in the formatting docs."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def scope_label(lang: str, kb_names: list[str] | None) -> str:
    """None = "all allowed"; otherwise the KB names in bold."""
    if kb_names is None:
        return t(lang, "all_allowed")
    return ", ".join(f"*{escape(name)}*" for name in kb_names)


def linked_sources(sources: list[Source]) -> list[Source]:
    """Sources a reader can open: without a url they are skipped, a repeated url counts once."""
    seen: set[str] = set()
    result: list[Source] = []
    for source in sources:
        if source.url and source.url not in seen:
            seen.add(source.url)
            result.append(source)
    return result


def source_lines(sources: list[Source]) -> list[str]:
    return [
        f"{source.label or index}. <{source.url}|{escape(truncate(source.title, 200))}>"
        for index, source in enumerate(sources, start=1)
    ]


def split_answer(text: str, limit: int = MAX_MARKDOWN) -> list[str]:
    """Split on paragraph boundaries into chunks of at most ``limit`` characters."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > limit:  # one paragraph larger than a whole message
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _context(text: str) -> Block:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": truncate(text, 3000)}]}


def _button(text: str, action_id: str, value: str) -> Block:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": text},
        "action_id": action_id,
        "value": value,
    }


def feedback_block(lang: str, completion_id: str) -> Block:
    """``feedback_buttons`` inside ``context_actions``, per the Block Kit reference."""
    return {
        "type": "context_actions",
        "elements": [
            {
                "type": "feedback_buttons",
                "action_id": ACTION_FEEDBACK,
                "positive_button": {
                    "text": {"type": "plain_text", "text": t(lang, "helpful")},
                    "value": f"up:{completion_id}",
                    "accessibility_label": t(lang, "helpful"),
                },
                "negative_button": {
                    "text": {"type": "plain_text", "text": t(lang, "not_helpful")},
                    "value": f"down:{completion_id}",
                    "accessibility_label": t(lang, "not_helpful"),
                },
            }
        ],
    }


def render_answer(result: ChatResult, kb_names: list[str], lang: str) -> RenderedAnswer:
    """One answer, split over several messages when it exceeds the markdown budget."""
    chunks = split_answer(result.text.strip() or "…")
    messages = [
        SlackMessage(text=truncate(chunk, MAX_TEXT), blocks=[{"type": "markdown", "text": chunk}])
        for chunk in chunks[:-1]
    ]

    sources = linked_sources(result.sources)
    last: list[Block] = [
        {"type": "markdown", "text": chunks[-1]},
        _context(t(lang, "searched_in", names=scope_label(lang, kb_names))),
    ]
    if sources:
        last.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": truncate("\n".join(source_lines(sources[:TOP_SOURCES])), MAX_SECTION),
                },
            }
        )
    buttons = [_button(t(lang, "pick_kbs"), ACTION_PICK, "pick")]
    if len(sources) > TOP_SOURCES and result.completion_id:
        remember_sources(result.completion_id, sources)
        buttons.append(
            _button(t(lang, "all_sources", n=len(sources)), ACTION_SOURCES, result.completion_id)
        )
    last.append({"type": "actions", "elements": buttons})
    if result.completion_id:
        last.append(feedback_block(lang, result.completion_id))
    messages.append(SlackMessage(text=truncate(chunks[-1], MAX_TEXT), blocks=last))
    return RenderedAnswer(messages=messages)


def render_notice(
    text: str,
    *,
    retry_label: str | None = None,
    retry_value: str = "",
    pick_label: str | None = None,
) -> RenderedAnswer:
    """A visible error or status message, optionally with a retry or a re-pick button."""
    blocks: list[Block] = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    buttons = []
    if retry_label:
        buttons.append(_button(retry_label, ACTION_RETRY, retry_value))
    if pick_label:
        buttons.append(_button(pick_label, ACTION_PICK, "pick"))
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
    return RenderedAnswer(messages=[SlackMessage(text=truncate(text, MAX_TEXT), blocks=blocks)])
