"""SPEC-KNOWLEDGE-ESCALATION-001 §3 — ticket subject and content text."""

from __future__ import annotations

import datetime as dt

from app.services.ticket_content import ReviewNote, TranscriptTurn, build_ticket_content, ticket_subject

T0 = dt.datetime(2026, 9, 14, 9, 12, tzinfo=dt.UTC)
LINK = "https://fictief.getklai.com/app/knowledge/activity/255"


def _turns(count: int, *, size: int = 20) -> list[TranscriptTurn]:
    turns = []
    for index in range(count):
        role = "user" if index % 2 == 0 else "assistant"
        turns.append(
            TranscriptTurn(
                role=role,
                content=f"beurt-{index} " + "x" * size,
                created_at=T0 + dt.timedelta(minutes=index),
                sources=[{"title": f"Bron {index}", "url": f"https://help.example.com/{index}"}]
                if role == "assistant"
                else [],
            )
        )
    return turns


def _build(turns: list[TranscriptTurn], **overrides: object) -> str:
    kwargs: dict[str, object] = {
        "notes": [ReviewNote(reviewer_name="Klaas Klai", reviewed_at=T0, note="Prijsvraag, graag bellen.")],
        "visitor_name": "Sam Jansen",
        "visitor_email": "sam@example.com",
        "started_at": T0,
        "widget_name": "Fictief help",
        "language": "nl",
        "verdict": "incomplete",
        "cause": "knowledge_missing",
        "turns": turns,
        "link": LINK,
        "available_until": dt.date(2026, 9, 21),
        "contact_found": True,
    }
    kwargs.update(overrides)
    return build_ticket_content(**kwargs)  # type: ignore[arg-type]


def test_content_carries_note_visitor_every_turn_sources_and_link() -> None:
    turns = _turns(4)
    content = _build(turns)

    assert content.startswith("Notities van de beoordelaar")
    assert "Klaas Klai" in content and "Prijsvraag, graag bellen." in content
    assert "Sam Jansen · sam@example.com" in content
    for turn in turns:
        assert turn.content in content
    assert "Bron 1 (https://help.example.com/1)" in content
    assert "https://help.example.com/3" in content
    assert f"Bekijk in Klai (beschikbaar tot 21-09-2026): {LINK}" in content


def test_unknown_contact_puts_the_visitor_on_the_first_line() -> None:
    """SPEC §3: when no contact could be created, the ticket itself must say
    who the visitor is before anything else."""
    content = _build(_turns(2), contact_found=False)
    assert content.splitlines()[0] == "Niet gevonden in HubSpot: Sam Jansen · sam@example.com"

    anonymous = _build(_turns(2), contact_found=False, visitor_name=None)
    assert anonymous.splitlines()[0] == "Niet gevonden in HubSpot: sam@example.com"


def test_truncation_drops_the_oldest_turns_first_and_says_so() -> None:
    turns = _turns(10, size=200)
    full = _build(turns)
    limit = len(full) - 300

    content = _build(turns, max_chars=limit)

    assert len(content) <= limit
    assert "beurt-0 " not in content
    assert "beurt-9 " in content
    assert "oudere berichten weggelaten" in content
    # Everything outside the transcript survives the cut.
    assert "Prijsvraag, graag bellen." in content and LINK in content


def test_subject_is_capped_at_100_characters() -> None:
    assert ticket_subject("Wat kost\nuitbreiding?") == "Webchat: Wat kost uitbreiding?"
    long = ticket_subject("a" * 300)
    assert long.startswith("Webchat: ")
    assert len(long) == len("Webchat: ") + 100
