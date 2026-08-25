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


def test_append_final_language_reminder_prefers_explicit_target() -> None:
    # Sol review P1: PDF attachment processing replaces the latest user
    # content with question + extracted document text BEFORE the reminder is
    # appended. The hook therefore detects the target on the unmutated
    # messages and passes it in; the passed target must win over whatever the
    # (mutated) messages would detect as.
    messages = [
        {
            "role": "user",
            "content": (
                "Please summarize this document in a few points.\n\n"
                "De klant heeft een probleem met de verbinding en kan niet "
                "bellen. Wij hebben dit ook met de beheerder getest en het "
                "werkt nog altijd niet. De monteur komt morgen langs."
            ),
        }
    ]

    target = append_final_language_reminder(
        messages, include_kb_reminder=False, target="en"
    )

    assert target == "en"
    assert "Respond in English" in messages[-1]["content"]


def test_append_final_language_reminder_explicit_unknown_uses_legacy_text() -> None:
    messages = [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        }
    ]

    target = append_final_language_reminder(
        messages, include_kb_reminder=False, target="und"
    )

    assert target == "und"
    assert messages[-1]["content"] == FINAL_RESPONSE_LANGUAGE_REMINDER
