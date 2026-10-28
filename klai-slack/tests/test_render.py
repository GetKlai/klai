from __future__ import annotations

from typing import Any

from app.clients.partner import ChatResult, Source
from app.conversation.render import (
    MAX_MARKDOWN,
    MAX_TEXT,
    render_answer,
    split_answer,
    stored_sources,
)


def _result(text: str = "Antwoord [1].", sources: list[Source] | None = None) -> ChatResult:
    return ChatResult(text=text, sources=sources or [], completion_id="cmpl-1", raw={})


def _types(blocks: list[dict[str, Any]]) -> list[str]:
    return [block["type"] for block in blocks]


def test_answer_message_layout_with_top_three_sources_deduplicated() -> None:
    sources = [
        Source(title="A", url="https://docs.example.com/a", label="1"),
        Source(title="A again", url="https://docs.example.com/a", label="2"),
        Source(title="No link", url=None, label="3"),
        Source(title="B", url="https://docs.example.com/b", label="4"),
        Source(title="C <x>", url="https://docs.example.com/c", label="5"),
        Source(title="D", url="https://docs.example.com/d", label="6"),
    ]
    (message,) = render_answer(_result(sources=sources), ["Handboek", "Support"], "nl").messages

    assert _types(message.blocks) == [
        "markdown",
        "context",
        "section",
        "actions",
        "context_actions",
    ]
    assert message.blocks[1]["elements"][0]["text"] == "Gezocht in: *Handboek*, *Support*"
    assert message.blocks[2]["text"]["text"] == (
        "1. <https://docs.example.com/a|A>\n"
        "4. <https://docs.example.com/b|B>\n"
        "5. <https://docs.example.com/c|C &lt;x&gt;>"
    )
    buttons = message.blocks[3]["elements"]
    assert [b["action_id"] for b in buttons] == ["thread_kb_pick", "sources_all"]
    assert buttons[1]["text"]["text"] == "Alle 4 bronnen"
    stored = stored_sources("cmpl-1")
    assert stored is not None
    assert [s.url for s in stored] == [f"https://docs.example.com/{x}" for x in "abcd"]


def test_feedback_buttons_shape() -> None:
    (message,) = render_answer(_result(), ["Handboek"], "nl").messages
    assert message.blocks[-1] == {
        "type": "context_actions",
        "elements": [
            {
                "type": "feedback_buttons",
                "action_id": "answer_feedback",
                "positive_button": {
                    "text": {"type": "plain_text", "text": "Nuttig"},
                    "value": "up:cmpl-1",
                    "accessibility_label": "Nuttig",
                },
                "negative_button": {
                    "text": {"type": "plain_text", "text": "Niet nuttig"},
                    "value": "down:cmpl-1",
                    "accessibility_label": "Niet nuttig",
                },
            }
        ],
    }


def test_refusal_without_sources_drops_the_sources_block_but_keeps_searched_in() -> None:
    (message,) = render_answer(_result("Dat staat er niet in."), ["Handboek"], "nl").messages
    assert _types(message.blocks) == ["markdown", "context", "actions", "context_actions"]
    assert [b["action_id"] for b in message.blocks[2]["elements"]] == ["thread_kb_pick"]


def test_long_answer_splits_on_paragraphs_with_sources_and_feedback_on_the_last() -> None:
    paragraphs = ["a" * 5000, "b" * 5000, "c" * 5000]
    sources = [Source(title="A", url="https://docs.example.com/a")]
    messages = render_answer(_result("\n\n".join(paragraphs), sources), ["H"], "nl").messages

    assert [m.blocks[0]["text"] for m in messages] == ["a" * 5000 + "\n\n" + "b" * 5000, "c" * 5000]
    assert _types(messages[0].blocks) == ["markdown"]
    assert _types(messages[1].blocks) == [
        "markdown",
        "context",
        "section",
        "actions",
        "context_actions",
    ]
    assert all(len(m.text) <= MAX_TEXT for m in messages)


def test_split_hard_cuts_a_paragraph_longer_than_a_message() -> None:
    chunks = split_answer("x" * (MAX_MARKDOWN + 10))
    assert [len(c) for c in chunks] == [MAX_MARKDOWN, 10]


def test_english_strings_for_an_english_locale() -> None:
    (message,) = render_answer(_result(), ["Handbook"], "en").messages
    assert message.blocks[1]["elements"][0]["text"] == "Searched: *Handbook*"
    assert message.blocks[2]["elements"][0]["text"]["text"] == "Choose knowledge bases"
    feedback = message.blocks[-1]["elements"][0]
    assert feedback["positive_button"]["text"]["text"] == "Helpful"
