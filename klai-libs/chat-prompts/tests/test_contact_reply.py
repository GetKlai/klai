"""The widget's fixed replies at a human name the contact route the turn picked."""

from __future__ import annotations

import pytest

from klai_chat_prompts import (
    RouteChannels,
    check_unavailable_message,
    check_unavailable_reply,
    closing_reply,
    contact_reply,
    contact_reply_again,
    not_found_reply,
    not_further_message,
    referral_reply,
    request_reply,
)

BOOKING = RouteChannels(name="Support", booking_url="https://book.example.com/s")
SALES = RouteChannels(name="Verkoop", email="verkoop@example.com", phone="010 000 0000")
_LINK = {"link_url": "https://help.example.com/facturen", "link_label": "Stel je vraag online"}
BILLING = RouteChannels(name="Facturen", note="Wij reageren binnen twee werkdagen.", **_LINK)

# The texts on origin/main before routes existed (dac3160ce), copied as literals so
# the comparison does not run through the code it checks.
_BEFORE = {
    "contact": {
        "nl": "Je kunt een afspraak maken met een medewerker, die helpt je persoonlijk verder. Gebruik daarvoor de "
        "knop onder dit bericht.",
        "en": "You can schedule an appointment with someone who will help you personally. Use the button under "
        "this message.",
    },
    "again": {
        "nl": "Via dit chatvenster kan ik alleen een afspraak voor je inplannen; iets anders kan ik hier niet regelen.",
        "en": "In this chat window I can only schedule an appointment for you; I can't arrange anything else here.",
    },
    "not_found": {
        "nl": "Dit vind ik niet terug in onze helpartikelen. Wil je het zeker weten, plan dan een afspraak met een "
        "medewerker. Die helpt je persoonlijk verder.",
        "en": "I can't find this in our help articles. If you want to be sure, schedule an appointment with someone "
        "who can help you personally.",
    },
    "not_further": {
        "nl": "De helpartikelen komen hier niet verder mee. Wil je het zeker weten, plan dan een afspraak met een "
        "medewerker. Die helpt je persoonlijk verder.",
        "en": "The help articles do not get any further on this. If you want to be sure, schedule an appointment "
        "with someone who can help you personally.",
    },
    "check_unavailable": {
        "nl": "Ik kan onze helpartikelen op dit moment niet nakijken, dus ik kan je vraag nu niet betrouwbaar "
        "beantwoorden. Probeer het straks nog eens, of plan een afspraak met een medewerker. Die helpt je "
        "persoonlijk verder.",
        "en": "I can't check our help articles right now, so I can't answer your question reliably at the moment. "
        "Please try again shortly, or schedule an appointment with someone who can help you personally.",
    },
    "referral": {
        "nl": "Over je factuur kijkt een collega graag persoonlijk met je mee. Plan hieronder een afspraak, dan helpen "
        "we je verder.",
        "en": "A colleague will gladly look at your invoice with you in person. Plan an appointment below and we will "
        "help you further.",
    },
}
_KINDS = {
    "contact": contact_reply,
    "again": contact_reply_again,
    "not_found": not_found_reply,
    "not_further": not_further_message,
    "check_unavailable": check_unavailable_reply,
}


@pytest.mark.parametrize("language", ["nl", "en"])
@pytest.mark.parametrize("kind", sorted(_KINDS))
@pytest.mark.parametrize("route", [None, BOOKING], ids=["no_routes", "booking_only"])
def test_a_route_with_only_a_booking_url_keeps_every_reply_as_it_was(
    route: RouteChannels | None, kind: str, language: str
) -> None:
    """The live widget's texts may not move when its settings become one route."""
    assert _KINDS[kind](language, route) == _BEFORE[kind][language]


@pytest.mark.parametrize("language", ["nl", "en"])
def test_a_referral_that_names_the_subject_reads_as_before_and_falls_back_to_the_routes_referral_text(
    language: str,
) -> None:
    subject = {"nl": "je factuur", "en": "your invoice"}[language]
    tenant_text = "Daarvoor kun je [een afspraak](https://book.example.com/s) maken, of mail info@example.com."
    route = RouteChannels(name="Support", booking_url="https://book.example.com/s", referral_text=tenant_text)

    assert referral_reply(language, subject, route) == _BEFORE["referral"][language]
    # Rendered as the old off_topic_reply was: the tenant's own text and nothing around it.
    assert referral_reply(language, None, route) == tenant_text
    # The note is a remark on every reply of the route, never the referral fallback.
    noted = RouteChannels(name="Support", booking_url="https://book.example.com/s", note="Op werkdagen.")
    assert referral_reply(language, None, noted).endswith("Op werkdagen.")
    assert referral_reply(language, None, noted) != "Op werkdagen."


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
    assert check_unavailable_message(language, helpdesk=True) == _BEFORE["check_unavailable"][language]
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


def test_a_request_says_the_chat_cannot_arrange_it_and_names_the_route():
    assert request_reply("nl", BOOKING, (SALES,)) == (
        "Dit kan ik niet zelf in deze chat regelen; een collega pakt het voor je op. "
        "Dit is een vraag voor Support. Plan hieronder een afspraak, dan helpen we je verder. "
        "Gaat het over iets anders? Verkoop: verkoop@example.com."
    )
    assert request_reply("en", SALES, (BOOKING,)) == (
        "I can't arrange this myself in this chat; a colleague will take care of it. "
        "This is a question for Verkoop. Call 010 000 0000 or email verkoop@example.com. "
        "Is it about something else? Support: [book an appointment](https://book.example.com/s)."
    )


def test_a_request_to_a_route_with_phone_and_link_and_no_booking_offers_no_appointment():
    route = RouteChannels(name="Facturen", phone="010 000 0000", **_LINK)

    text = request_reply("nl", route)

    assert "afspraak" not in text
    assert text.startswith(
        "Dit kan ik niet zelf in deze chat regelen; een collega pakt het voor je op. Bel 010 000 0000"
    )
    assert "[Stel je vraag online](https://help.example.com/facturen)" in text


def test_a_request_on_a_widget_without_routes_points_at_the_button_it_always_had():
    assert request_reply("nl") == (
        "Dit kan ik niet zelf in deze chat regelen; een collega pakt het voor je op. "
        "Plan hieronder een afspraak, dan helpen we je verder."
    )


def test_a_closing_claims_nothing_and_names_no_route_channel_or_button():
    assert closing_reply("nl") == "Helder. Heb je nog een vraag, stel hem gerust."
    assert closing_reply("en") == "All right. If you have another question, just ask."
