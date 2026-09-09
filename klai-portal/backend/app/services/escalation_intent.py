"""Escalation intent, decided by the backend and not by the answer model.

Fin, Zendesk and Ada treat "the visitor wants a person" and "the visitor is
frustrated" as signals detected beside the answer, which then force the
handoff regardless of what the knowledge base returned. Until this module the
help-page widget had no such layer: the appointment button appeared only when
the model happened to write its marker, and one matching article was enough
to make it write steps instead. Measured on 2026-09-09: "IK WIL EEN MEDEWERKER
SPREKEN" with conversation history retrieved a staff routing script at
reranker 0.74 and produced no offer at all.

Two intents, both language-neutral for NL/EN, deliberately conservative:

* ``human_request`` — the visitor asks to reach a person.
* ``frustration`` — repeated complaint or an angry register.

Known ceiling: this is a pattern heuristic, not a classifier. Its upgrade path
is the LLM-as-judge pass from the measurement plan, which can read the whole
conversation. Until then a miss here still leaves the two other sources of the
signal in place (the canned refusal and the model's marker).
"""

from __future__ import annotations

import re

HUMAN_REQUEST = "human_request"
FRUSTRATION = "frustration"

# Asking for a person. Word-boundary anchored so "medewerkersportaal" or
# "humanitarian" cannot trip it; the verb list keeps "de medewerker vertelde"
# (a report) apart from "ik wil een medewerker" (a request).
_HUMAN_RE = re.compile(
    r"\b("
    r"(ik )?(wil|kan|mag|moet|zou)( graag| gewoon| nu| even| toch)? "
    r"(een |de |met (een |de )?)?(mens|medewerker|persoon|iemand|collega|mensen|echt iemand|echt persoon)\b"
    r"|(spreek|spreken|praten|bellen|contact)( met)? (een |de )?(mens|medewerker|persoon|iemand|collega)\b"
    r"|(hulp|contact) (van|met) (een |de )?(mens|medewerker|persoon|iemand|collega)\b"
    r"|(kan|mag|zou|wil) ik( graag| even| nu| toch)?( met)? (iemand|een (mens|medewerker|persoon|collega)|de (medewerker|collega))"
    r"( even| nu| toch)? (spreken|praten|bellen|hebben)\b"
    r"|(speak|talk|chat)( to| with) (a |an |the )?(human|person|agent|someone|real person|representative|employee)\b"
    r"|(i (want|need|would like)( to)?) (a |an )?(human|person|agent|real person|representative)\b"
    r"|geen (chatbot|bot|robot)\b"
    r"|no(t a)? (chatbot|bot|robot)\b"
    r")",
    re.IGNORECASE,
)

# An angry register. Each alternative alone is enough; the words are the ones
# the Voys tone research lists as the visitor's own frustration vocabulary.
_FRUSTRATION_RE = re.compile(
    r"\b("
    r"ik ben (het|dit|er) (echt |helemaal |zo )?(zat|beu|klaar mee)"
    r"|belachelijk|schandalig|waardeloos|ridiculous|unacceptable|useless"
    r"|niemand (helpt|lost .* op|reageert)|nobody (helps|answers|fixes)"
    r"|(al|nu) (drie|vier|vijf|3|4|5|zes|tien) keer"
    r"|(third|fourth|fifth) time"
    r")",
    re.IGNORECASE,
)

# Shouting: a message that is mostly capitals and long enough to mean it.
_MIN_SHOUT_LETTERS = 12


def _is_shouting(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < _MIN_SHOUT_LETTERS:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.8


def escalation_intent(text: object) -> str | None:
    """Return ``human_request``, ``frustration`` or ``None`` for one message."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if _HUMAN_RE.search(stripped):
        return HUMAN_REQUEST
    if _FRUSTRATION_RE.search(stripped) or _is_shouting(stripped):
        return FRUSTRATION
    return None


# Appended to the system prompt for the one turn where an intent fired. It
# tells the model what the backend already decided, so the reply matches the
# button the widget is about to render under it. English, like the profile.
ESCALATION_TURN_ADDENDUM = {
    HUMAN_REQUEST: (
        "\n\n[This turn] The visitor just asked to reach a person. Do NOT answer with "
        "steps or an article, even if one matches. Acknowledge in one sentence, then "
        "offer the appointment with a human employee as the action they can take now. "
        "Name no phone number, e-mail address or URL. End the reply with the exact token "
        "[[APPOINTMENT_OFFER]] on its own final line."
    ),
    FRUSTRATION: (
        "\n\n[This turn] The visitor is frustrated. Keep any answer short, then offer the "
        "appointment with a human employee as the action they can take now. Name no phone "
        "number, e-mail address or URL. End the reply with the exact token "
        "[[APPOINTMENT_OFFER]] on its own final line."
    ),
}
