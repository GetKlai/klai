"""The widget's fixed replies at a human name the contact route the turn picked."""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    RouteChannels,
    check_unavailable_message,
    check_unavailable_reply,
    contact_reply,
    contact_reply_again,
    no_citable_sources_message,
    not_found_reply,
    not_further_message,
    referral_reply,
)

BOOKING = RouteChannels(name="Support", booking_url="https://book.example.com/s")
SALES = RouteChannels(name="Verkoop", email="verkoop@example.com", phone="010 000 0000")
_LINK = {"link_url": "https://help.example.com/facturen", "link_label": "Stel je vraag online"}
BILLING = RouteChannels(name="Facturen", note="Wij reageren binnen twee werkdagen.", **_LINK)


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


def test_a_route_without_a_booking_url_opens_with_its_first_channel_as_the_way_in() -> None:
    """ "Bellen kan ook" only reads after the appointment sentence; without one the channel is the instruction."""
    assert contact_reply("nl", SALES) == (
        "Een medewerker helpt je graag persoonlijk verder. Bel 010 000 0000 of mail naar verkoop@example.com."
    )
    assert contact_reply("en", RouteChannels(name="Verkoop", phone="010 000 0000", **_LINK)) == (
        "Someone will gladly help you personally. Call 010 000 0000. "
        "Or use [Stel je vraag online](https://help.example.com/facturen)."
    )
    assert not_found_reply("en", BILLING) == (
        "I can't find this in our help articles. If you want to be sure, someone can help you personally. "
        "Use [Stel je vraag online](https://help.example.com/facturen). Wij reageren binnen twee werkdagen."
    )


def test_with_several_routes_the_reply_says_which_one_it_names_and_with_one_it_does_not() -> None:
    assert not_found_reply("nl", SALES, (BOOKING,)).startswith(
        "Dit vind ik niet terug in onze helpartikelen. Dit is een vraag voor Verkoop. Wil je het zeker weten"
    )
    assert contact_reply("en", BOOKING, (SALES,)).startswith(
        "This is a question for Support. You can schedule an appointment"
    )
    assert referral_reply("nl", "je factuur", BOOKING, (SALES,)).startswith(
        "Over je factuur kijkt een collega graag persoonlijk met je mee. Dit is een vraag voor Support. Plan hieronder"
    )
    assert "Verkoop" not in contact_reply("nl", SALES)


@pytest.mark.parametrize("language", ["nl", "en"])
def test_the_check_unavailable_text_offers_an_appointment_only_when_the_route_books(language: str) -> None:
    assert check_unavailable_reply(language, BOOKING) == check_unavailable_message(language, helpdesk=True)
    without = check_unavailable_reply(language, SALES)
    assert "afspraak" not in without and "appointment" not in without
    assert without.endswith(
        ("Bel 010 000 0000 of mail naar verkoop@example.com.", "Call 010 000 0000 or email verkoop@example.com.")
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
