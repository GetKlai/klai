from __future__ import annotations

from klai_chat_prompts import KB_CONTEXT_LANGUAGE_REMINDER
from klai_kb_system_prompt import (
    FINAL_RESPONSE_LANGUAGE_REMINDER,
    append_final_language_reminder,
    final_response_language_reminder,
)

_EN_TURN = {
    "role": "user",
    "content": "Could you please explain what this setting does today?",
}


def test_append_final_language_reminder_uses_explicit_english_target() -> None:
    messages = [dict(_EN_TURN)]

    target = append_final_language_reminder(
        messages, include_kb_reminder=False, target="en"
    )

    assert target == "en"
    assert messages[-1]["role"] == "system"
    assert "Respond in English" in messages[-1]["content"]
    assert "substantive message is in English (en)" in messages[-1]["content"]


def test_append_final_language_reminder_without_target_uses_generic_text() -> None:
    # No self-detection anymore: a missing target means the conversation
    # abstained (or the decision was withheld) and the generic reminder is
    # the honest contract — the model-side prompt rules take over.
    messages = [{"role": "user", "content": "Hi"}]

    target = append_final_language_reminder(messages, include_kb_reminder=False)

    assert target is None
    assert messages[-1]["content"] == FINAL_RESPONSE_LANGUAGE_REMINDER


def test_append_final_language_reminder_keeps_kb_reminder_composition() -> None:
    messages = [dict(_EN_TURN)]

    target = append_final_language_reminder(messages, target="en")

    assert target == "en"
    assert messages[-1]["content"] == (
        f"{final_response_language_reminder('en')}\n\n{KB_CONTEXT_LANGUAGE_REMINDER}"
    )


def test_append_final_language_reminder_is_idempotent_for_explicit_variant() -> None:
    messages = [dict(_EN_TURN)]

    first_target = append_final_language_reminder(
        messages, include_kb_reminder=False, target="en"
    )
    second_target = append_final_language_reminder(
        messages, include_kb_reminder=False, target="en"
    )

    assert first_target == "en"
    assert second_target == "en"
    assert len([m for m in messages if m["role"] == "system"]) == 1


def test_append_final_language_reminder_is_idempotent_for_generic_variant() -> None:
    messages = [
        dict(_EN_TURN),
        {"role": "system", "content": FINAL_RESPONSE_LANGUAGE_REMINDER},
    ]

    target = append_final_language_reminder(messages, include_kb_reminder=False)

    assert target is None
    assert messages == [
        dict(_EN_TURN),
        {"role": "system", "content": FINAL_RESPONSE_LANGUAGE_REMINDER},
    ]


def test_append_final_language_reminder_prefers_explicit_target() -> None:
    # Sol review P1: PDF attachment processing replaces the latest user
    # content with question + extracted document text BEFORE the reminder is
    # appended. The hook therefore takes the conversation decision on the
    # UNMUTATED messages and passes the code in; this function never reads
    # message content to detect a language itself.
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


def test_append_final_language_reminder_other_language_target_names_it() -> None:
    messages = [dict(_EN_TURN)]

    target = append_final_language_reminder(
        messages, include_kb_reminder=False, target="de"
    )

    assert target == "de"
    assert "Respond in German" in messages[-1]["content"]
