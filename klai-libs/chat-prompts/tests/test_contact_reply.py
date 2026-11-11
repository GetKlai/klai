"""``contact_reply`` names the widget's own channels beside the appointment sentence."""

from __future__ import annotations

from klai_chat_prompts import contact_reply, not_further_message


def test_english_reply_names_both_channels_after_the_appointment_sentence() -> None:
    assert contact_reply("en", email="hallo@example.com", phone="000-0000000") == (
        "You can schedule an appointment with someone who will help you personally. Use the button under this "
        "message. You can also email us: hallo@example.com. You can also call us: 000-0000000."
    )


def test_a_single_channel_adds_a_single_sentence() -> None:
    assert contact_reply("nl", phone="000-0000000").endswith("onder dit bericht. Bellen kan ook: 000-0000000.")


def test_the_not_further_reply_names_the_appointment_and_then_the_channels() -> None:
    assert not_further_message("en", email="hallo@example.com", phone="000-0000000") == (
        "The help articles do not get any further on this. If you want to be sure, schedule an appointment with "
        "someone who can help you personally. You can also email us: hallo@example.com. "
        "You can also call us: 000-0000000."
    )
    assert not_further_message("nl").startswith("De helpartikelen komen hier niet verder mee.")
