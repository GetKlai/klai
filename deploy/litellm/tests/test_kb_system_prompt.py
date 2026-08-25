from __future__ import annotations

from klai_chat_prompts import KB_CONTEXT_LANGUAGE_REMINDER
from klai_kb_system_prompt import (
    FINAL_RESPONSE_LANGUAGE_REMINDER,
    append_final_language_reminder,
    final_response_language_reminder,
)


def test_append_final_language_reminder_uses_explicit_english_target() -> None:
    messages = [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        }
    ]

    target = append_final_language_reminder(messages, include_kb_reminder=False)

    assert target == "en"
    assert messages[-1]["role"] == "system"
    assert "Respond in English" in messages[-1]["content"]
    assert "substantive message is in English (en)" in messages[-1]["content"]


def test_append_final_language_reminder_falls_back_to_legacy_text_for_unknown() -> None:
    messages = [{"role": "user", "content": "Hi"}]

    target = append_final_language_reminder(messages, include_kb_reminder=False)

    assert target == "und"
    assert messages[-1]["content"] == FINAL_RESPONSE_LANGUAGE_REMINDER


def test_append_final_language_reminder_keeps_kb_reminder_composition() -> None:
    messages = [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        }
    ]

    target = append_final_language_reminder(messages)

    assert target == "en"
    assert messages[-1]["content"] == (
        f"{final_response_language_reminder('en')}\n\n{KB_CONTEXT_LANGUAGE_REMINDER}"
    )


def test_append_final_language_reminder_is_idempotent_for_explicit_variant() -> None:
    messages = [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        }
    ]

    first_target = append_final_language_reminder(messages, include_kb_reminder=False)
    second_target = append_final_language_reminder(messages, include_kb_reminder=False)

    assert first_target == "en"
    assert second_target == "en"
    assert len([m for m in messages if m["role"] == "system"]) == 1


def test_append_final_language_reminder_is_idempotent_for_legacy_variant() -> None:
    messages = [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        },
        {"role": "system", "content": FINAL_RESPONSE_LANGUAGE_REMINDER},
    ]

    target = append_final_language_reminder(messages, include_kb_reminder=False)

    assert target == "en"
    assert messages == [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        },
        {"role": "system", "content": FINAL_RESPONSE_LANGUAGE_REMINDER},
    ]
