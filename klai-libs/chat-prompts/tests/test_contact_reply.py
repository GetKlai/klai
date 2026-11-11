"""The widget's fixed replies at a human name the contact route the turn picked."""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    RouteChannels,
    contact_reply,
    contact_reply_again,
    no_citable_sources_message,
    not_found_reply,
    not_further_message,
    referral_reply,
)

BOOKING = RouteChannels(name="Support", booking_url="https://book.example.com/s")
SALES = RouteChannels(name="Verkoop", email="verkoop@example.com", phone="010 000 0000")
BILLING = RouteChannels(
    name="Facturen",
    link_url="https://help.example.com/facturen",
    link_label="Stel je vraag online",
    note="Wij reageren binnen twee werkdagen.",
)


@pytest.mark.parametrize("route", [None, BOOKING], ids=["no_routes", "booking_only"])
def test_a_route_with_only_a_booking_url_keeps_every_reply_as_it_was(route: RouteChannels | None) -> None:
    """The live widget's texts may not move when its settings become one route."""
    assert contact_reply("nl", route) == (
        "Je kunt een afspraak maken met een medewerker, die helpt je persoonlijk verder. Gebruik daarvoor de knop "
        "onder dit bericht."
    )
    assert contact_reply_again("en", route) == (
        "In this chat window I can only schedule an appointment for you; I can't arrange anything else here."
    )
    assert not_found_reply("nl", route) == no_citable_sources_message("nl", helpdesk=True)
    assert not_found_reply("en", route) == no_citable_sources_message("en", helpdesk=True)
    assert not_further_message("en", route) == (
        "The help articles do not get any further on this. If you want to be sure, schedule an appointment with "
        "someone who can help you personally."
    )
    assert not_further_message("nl", route) == (
        "De helpartikelen komen hier niet verder mee. Wil je het zeker weten, plan dan een afspraak met een "
        "medewerker. Die helpt je persoonlijk verder."
    )
    # A referral needs a route that names subjects, so only the booking route has one.
    assert referral_reply("nl", "je factuur", BOOKING) == (
        "Over je factuur kijkt een collega graag persoonlijk met je mee. Plan hieronder een afspraak, dan helpen we "
        "je verder."
    )


def test_the_referral_falls_back_to_the_routes_own_note_verbatim() -> None:
    route = RouteChannels(name="Support", booking_url="https://book.example.com/s", note="Daarvoor bel je ons even.")

    assert referral_reply("nl", None, route) == "Daarvoor bel je ons even."
    # The note is the fallback, so a referral that names the subject stays as it was.
    assert referral_reply("nl", "je factuur", route) == referral_reply("nl", "je factuur", BOOKING)


def test_a_route_without_a_booking_url_names_its_channels_and_no_appointment() -> None:
    assert contact_reply("nl", SALES) == (
        "Een medewerker helpt je graag persoonlijk verder. Mailen kan ook: verkoop@example.com. "
        "Bellen kan ook: 010 000 0000."
    )
    assert not_found_reply("en", BILLING) == (
        "I can't find this in our help articles. If you want to be sure, someone can help you personally. "
        "See also: [Stel je vraag online](https://help.example.com/facturen). Wij reageren binnen twee werkdagen."
    )


@pytest.mark.parametrize(
    ("language", "others_line"),
    [
        (
            "nl",
            "Gaat het over iets anders? Verkoop: verkoop@example.com; Support: [afspraak maken](https://book.example.com/s).",
        ),
        (
            "en",
            "Is it about something else? Verkoop: verkoop@example.com; Support: [book an appointment](https://book.example.com/s).",
        ),
    ],
)
def test_several_routes_end_on_the_others_by_name_with_their_first_channel(language: str, others_line: str) -> None:
    reply = not_further_message(language, BILLING, others=(SALES, BOOKING))

    assert reply.endswith(others_line)
    assert "[Stel je vraag online](https://help.example.com/facturen)" in reply
    assert "afspraak met een medewerker" not in reply and "schedule an appointment" not in reply
