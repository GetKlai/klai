"""Plain-text ticket body for a reviewed conversation — SPEC-KNOWLEDGE-ESCALATION-001 §3.

Pure functions, no I/O: the route gathers the rows, this module decides the
text. The ticket has to carry enough for the receiving team to continue
without Klai, because the conversation itself is purged after the retention
window; so the whole transcript goes along, and only when HubSpot's property
limit forces it are the oldest turns dropped (the newest turns are what the
visitor still needs an answer on).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.hubspot_tickets import TICKET_CONTENT_MAX_CHARS

_SUBJECT_PREFIX = "Webchat: "
_SUBJECT_QUESTION_CHARS = 100
# "HH:MM  Bezoeker  " — continuation lines and source lines align under the text.
_TURN_INDENT = " " * 18
_ROLE_LABEL = {"user": "Bezoeker", "assistant": "Klai    "}
_VERDICT_LABEL = {"correct": "correct", "incomplete": "onvolledig", "wrong": "fout", "not_a_fault": "geen fout"}
_CAUSE_LABEL = {
    "knowledge_missing": "kennis ontbreekt",
    "knowledge_wrong": "kennis onjuist",
    "behaviour": "gedrag van de assistent",
}


@dataclass(frozen=True)
class ReviewNote:
    reviewer_name: str | None
    reviewed_at: dt.datetime | None
    note: str


@dataclass(frozen=True)
class TranscriptTurn:
    role: str
    content: str
    created_at: dt.datetime
    sources: Sequence[dict[str, Any]] = field(default_factory=tuple)


def _utf16_len(text: str) -> int:
    # HubSpot's limit is enforced by a Java backend, which counts UTF-16 code
    # units; an emoji is two of those but one Python character.
    return len(text.encode("utf-16-le")) // 2


def _date(value: dt.datetime | dt.date) -> str:
    return value.strftime("%d-%m-%Y")


def _time(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime("%H:%M")


def ticket_subject(first_question: str | None) -> str:
    question = " ".join((first_question or "").split())
    return _SUBJECT_PREFIX + question[:_SUBJECT_QUESTION_CHARS]


def _turn_lines(turn: TranscriptTurn) -> list[str]:
    text_lines = turn.content.strip().splitlines() or [""]
    lines = [f"  {_time(turn.created_at)}  {_ROLE_LABEL.get(turn.role, turn.role)}  {text_lines[0]}"]
    lines.extend(f"{_TURN_INDENT}{line}" for line in text_lines[1:])
    sources = [
        f"{source.get('title') or source.get('url')} ({source['url']})"
        if source.get("url")
        else str(source.get("title"))
        for source in turn.sources
        if source.get("url") or source.get("title")
    ]
    if sources:
        lines.append(f"{_TURN_INDENT}Bronnen: {', '.join(sources)}")
    return lines


def build_ticket_content(
    *,
    notes: Sequence[ReviewNote],
    visitor_name: str | None,
    visitor_email: str,
    started_at: dt.datetime,
    widget_name: str | None,
    language: str | None,
    verdict: str | None,
    cause: str | None,
    turns: Sequence[TranscriptTurn],
    link: str,
    available_until: dt.date,
    contact_found: bool,
    max_chars: int = TICKET_CONTENT_MAX_CHARS,
) -> str:
    visitor = " · ".join(p for p in (visitor_name, visitor_email) if p)
    head: list[str] = []
    if not contact_found:
        # HubSpot refused to create the contact (SPEC §2.6, a key without
        # crm.objects.contacts.write), so the ticket carries no contact and
        # the team needs the visitor's details on the very first line.
        head += [f"Niet gevonden in HubSpot: {visitor}", ""]
    if notes:
        head.append("Notities van de beoordelaar")
        for note in notes:
            who = ", ".join(p for p in (note.reviewer_name, note.reviewed_at and _date(note.reviewed_at)) if p)
            note_lines = note.note.strip().splitlines()
            head.append(f"  {who}: {note_lines[0]}" if who else f"  {note_lines[0]}")
            head.extend(f"    {line}" for line in note_lines[1:])
        head.append("")
    head += ["Bezoeker", f"  {visitor}", ""]
    head += [
        "Gesprek",
        "  "
        + " · ".join(
            p for p in (started_at.astimezone(dt.UTC).strftime("%d-%m-%Y %H:%M UTC"), widget_name, language) if p
        ),
    ]
    if verdict:
        judged = [_VERDICT_LABEL.get(verdict, verdict)]
        if cause and cause in _CAUSE_LABEL:
            judged.append(_CAUSE_LABEL[cause])
        head.append("  Beoordeling: " + " · ".join(judged))
    head.append("")
    tail = ["", f"Bekijk in Klai (beschikbaar tot {_date(available_until)}): {link}"]

    blocks = [_turn_lines(turn) for turn in turns]
    dropped = 0
    while True:
        body = [line for block in blocks[dropped:] for line in block]
        if dropped:
            body.insert(0, f"  ({dropped} oudere berichten weggelaten: het ticket past anders niet in HubSpot)")
        text = "\n".join(head + body + tail)
        if _utf16_len(text) <= max_chars or dropped >= len(blocks):
            break
        dropped += 1
    # Only reachable when the notes alone exceed the limit; a hard cut still
    # beats HubSpot rejecting the whole ticket.
    return text.encode("utf-16-le")[: max_chars * 2].decode("utf-16-le", errors="ignore")
