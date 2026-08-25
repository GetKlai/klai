from __future__ import annotations

from klai_language_detect import (
    UNKNOWN_LANGUAGE,
    detect_language,
    detect_response_language,
    latest_substantive_user_text,
)


def test_detect_language_happy_paths() -> None:
    cases = {
        "nl": "Kunt je graag uitleggen wat dit is en hoe wij dit gebruiken?",
        "en": "Could you please explain what this is and how you use it?",
        "de": "Bitte können wir das mit dem Konto und der App prüfen?",
        "fr": "Pouvez vous nous aider avec les options pour la connexion?",
        "pt": "Você pode explicar que isso é para nós, por favor?",
        "es": "Usted puede explicar que esto es para nosotros con detalle?",
    }

    for expected, text in cases.items():
        assert detect_language(text) == expected


def test_detect_language_unknown_cases() -> None:
    assert detect_language("hoi") == UNKNOWN_LANGUAGE
    assert detect_language("de de de de de de de de de de de de de de de") == UNKNOWN_LANGUAGE
    assert detect_language("") == UNKNOWN_LANGUAGE


def test_latest_substantive_user_text_uses_single_short_first_message() -> None:
    assert latest_substantive_user_text([{"role": "user", "content": "Zoom?"}]) == "Zoom?"


def test_latest_substantive_user_text_ignores_trailing_short_thanks() -> None:
    messages = [
        {
            "role": "user",
            "content": "Could you please explain what this setting does today?",
        },
        {"role": "assistant", "content": "Sure."},
        {"role": "user", "content": "dank je"},
    ]

    assert (
        latest_substantive_user_text(messages)
        == "Could you please explain what this setting does today?"
    )


def test_latest_substantive_user_text_picks_full_language_switch() -> None:
    messages = [
        {
            "role": "user",
            "content": "Kunt je uitleggen wat deze instelling vandaag precies doet?",
        },
        {
            "role": "user",
            "content": "Does Outlook work with Voys for calendar sync?",
        },
    ]

    assert detect_response_language(messages) == "en"


def test_latest_substantive_user_text_handles_list_parts() -> None:
    messages = [
        {"role": "assistant", "content": "Hi"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Bitte können wir das mit dem Konto"},
                {"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
                {"type": "text", "text": "und der App prüfen?"},
            ],
        },
    ]

    assert latest_substantive_user_text(messages) == (
        "Bitte können wir das mit dem Konto und der App prüfen?"
    )
    assert detect_response_language(messages) == "de"


def test_latest_substantive_user_text_empty_without_user_messages() -> None:
    assert latest_substantive_user_text([{"role": "assistant", "content": "Hi"}]) == ""
