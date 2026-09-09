"""Conversation-level response-language decisions for Klai chat surfaces.

Language is a property of the CONVERSATION, not of a single message. Every
Klai chat surface resends the full history on each request, so the decision
is derived by replaying the user turns in order: pure, stateless, no cache,
same messages in, same decision out.

This module is the single language-identification mechanism behind every Klai
chat surface (it replaced and deleted the per-message stopword counter that
used to live in ``deploy/litellm/klai_language_detect.py``). Identification is
delegated to ``langid`` (BSD, pure Python, model embedded), restricted to
:data:`TARGET_LANGUAGES` with normalised probabilities — except for surviving
prose too short for langid's confidence to mean anything, which is decided
deterministically from unambiguous function words (see
:data:`SHORT_PROSE_MAX_WORDS`).

Surfaces that hold a conversation list use :func:`resolve_conversation_language`;
surfaces that only hold a single text (a lone query, an answer to measure) use
:func:`identify_text_language`, which runs the same gate and identifier on that
one text. One implementation, two entry points — a call site MUST NOT roll its
own guessing.

Design contract
---------------
1. **Evidence gate first.** Machine text (logs, code, mail headers, tables,
   pasted JSON) contains real English words, so a language identifier labels
   it confidently — and that label must never count. :func:`classify_turn_evidence`
   strips machine content and abstains when too little prose survives —
   absolutely (:data:`MIN_PROSE_WORDS`) AND proportionally: when a clear
   majority of the turn's lines are machine-like, a handful of readable words
   rescued from the remaining lines is not prose, no matter how many of them
   clear the absolute floor (:data:`MIN_PROSE_SENTENCE_WORDS` rescue rule).
   The gate must never depend on the identifier being unsure of the debris
   to do its job. An abstaining turn carries NO vote: it never sets, switches
   or resets anything. (Explicit requests are the one signal replay reads
   from gate-failing turns, because those turns are short by design — see
   step 2.)
2. **Explicit language requests win outright.** "Answer in Dutch please" sets
   the language with maximum weight regardless of hysteresis or turn strength.
   These turns are short by nature; no minimum-word rule may discard them.
 3. **Hysteresis: switching always costs more evidence than establishing.**
    Establishing a language when none is set is cheap: any turn that passes the
    gate and the confidence tiers establishes, even a weak one. Changing an
    already established language is never free and costs a STRONG turn. The
    distinction is TALKING versus QUOTING, not quantity:
    :func:`_turn_is_strong_switch` judges a turn weak the moment it wraps
    anything in quotes or names a technical identifier — a log line, an error
    string, a field name — because quoting machine output says nothing about
    which language the user wants to be ANSWERED in, however clean the sentence
    around it is; a bare number is not an identifier. Inside the opening window
    (:data:`OPENING_WINDOW_TURNS` voting turns) a switch costs ONE strong turn;
    outside it a strong turn AND the confirmation streak
    (:data:`FIRST_SWITCH_CONFIRMATIONS` / :data:`SUBSEQUENT_SWITCH_CONFIRMATIONS`).
    Weak votes against the established language are inert: they cannot switch
    and cannot advance a streak at any point, but they still count as a vote —
    a weak turn can confirm the current language and can open a conversation
    when nothing is set yet.
4. **Never raise.** If ``langid`` cannot be imported (or the container has no
   vendored copy), every turn that needs identification abstains and the
   caller falls back to model-side detection. Returning ``language=None`` is
   always safer than returning a guess.

Only ``role == "user"`` turns vote. Assistant messages, system prompts and
retrieved context never influence the decision.

Vendoring
---------
``deploy/litellm/klai_conversation_language.py`` is a byte-for-byte copy of
this file (the LiteLLM container bind-mounts flat ``.py`` files and cannot
install the package). The drift test
``deploy/litellm/tests/test_klai_conversation_language_drift.py`` fails if the
two ever diverge; edit the canonical file here and re-copy.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

__all__ = [
    "EVIDENCE_EMPTY",
    "EVIDENCE_MACHINE",
    "EVIDENCE_PROSE",
    "EXPLICIT_LANGUAGE_NAMES",
    "FIRST_SWITCH_CONFIRMATIONS",
    "IDENTIFY_MIN_CONFIDENCE",
    "IDENTIFY_MIN_CONFIDENCE_SHORT",
    "LONG_PROSE_WORDS",
    "MAX_EXPLICIT_REQUEST_WORDS",
    "METHOD_EXPLICIT_REQUEST",
    "METHOD_FUNCTION_WORDS",
    "METHOD_LANGID",
    "MIN_PROSE_SENTENCE_WORDS",
    "MIN_PROSE_WORDS",
    "OPENING_WINDOW_TURNS",
    "REASON_DETECTOR_UNAVAILABLE",
    "REASON_ESTABLISHED",
    "REASON_EXPLICIT_REQUEST",
    "REASON_LOCKED",
    "REASON_LOW_CONFIDENCE",
    "REASON_NO_EVIDENCE",
    "REASON_SWITCHED",
    "SHORT_PROSE_MAX_WORDS",
    "SUBSEQUENT_SWITCH_CONFIRMATIONS",
    "TARGET_LANGUAGES",
    "LanguageDecision",
    "TurnEvidence",
    "classify_turn_evidence",
    "detect_explicit_language_request",
    "identify_text_language",
    "resolve_conversation_language",
]

# The six languages Klai answers in. Everything (identifier restriction,
# explicit-request table, return values) is confined to this tuple.
TARGET_LANGUAGES: tuple[str, ...] = ("nl", "en", "de", "fr", "pt", "es")

# Evidence gate: minimum word tokens that must survive machine-content removal
# before a turn is judged at all ("too little prose remains to judge"). Chosen
# as 3: on measured production-like traffic langid restricted to six languages
# mislabels short acknowledgements at usable-looking confidence ("Dank je wel"
# → fr 0.83, "Precies, dank je wel" → fr 0.83, "Klant schreef" → de 0.92),
# while genuinely language-bearing short questions still reach > 0.999 ("Hoe
# werkt dit" → nl, "What is this" → en) and are covered by the short-text
# confidence tier below. NOTE: explicit language requests (step 2) are exempt
# from this floor — replay matches the surviving prose against the request
# table even for gate-abstaining turns, so a bare "Duits?" still switches.
MIN_PROSE_WORDS = 3

# Step 3: langid (norm_probs=True) confidence required for a turn to vote.
# 0.70 is a starting value for normal-length prose (>= LONG_PROSE_WORDS
# words): measured, clear sentences score >= 0.99 and ambiguous fragments
# <= 0.6, so it separates the bands with room to spare. Tune against real
# traffic before changing.
IDENTIFY_MIN_CONFIDENCE = 0.70

# Prose shorter than this (but >= MIN_PROSE_WORDS) is language-judgeable only
# at near-certainty, because a handful of cross-language look-alike words
# ("dank je", "dat is") flip langid's top class with high normalised
# confidence. Measured correct short questions land at >= 0.9999; measured
# false positives top out around 0.99.
LONG_PROSE_WORDS = 7

# Confidence required for 3-6 word prose (between MIN_PROSE_WORDS and
# LONG_PROSE_WORDS). See LONG_PROSE_WORDS.
IDENTIFY_MIN_CONFIDENCE_SHORT = 0.99

# Prose of at most this many words (gate survivors, protocol acronyms
# stripped — the same measure as the confidence tiers above) is decided
# deterministically from the unambiguous function-word tables below before
# langid is consulted at all. "Short" is pinned by measurement: langid
# carries no reliable signal this thin (7 of 12 measured Dutch widget
# questions abstained outright; "Wat is TCP/IP?" sat at en@0.98 against the
# 0.99 bar), while at 9+ measured words its tiered confidence already
# decided correctly. 8 is the largest measured failing length; longer prose
# keeps today's behaviour unchanged.
SHORT_PROSE_MAX_WORDS = 8

# Step 4: number of voting turns before the conversation language locks.
# Inside the window a language switch costs one strong turn (see
# _turn_is_strong_switch) instead of the full confirmation streak that switches
# cost after locking — but never zero: even here a weak vote cannot change the
# established language.
OPENING_WINDOW_TURNS = 3

# Gate, proportional rule (correction 2): when more than half of the turn's
# non-empty content lines are machine-like, the turn abstains UNLESS one
# single surviving prose line carries at least this many SENTENCE words (see
# _sentence_word_count: space-separated word tokens, not letter runs inside
# attribute syntax). The line can only then be the user's own running
# sentence — fragments rescued from a mechanical paste ("HTTP/1.1 503 Service
# Unavailable") never reach this length, and neither do single machine
# attribute lines, whose embedded fragments ("a=rtcp-xr:rcvr-rtt=all:10000
# stat-summary=loss,dup,jitt,TTL voip-metrics" — 14 letter runs, one word)
# the raw letter-run count used to pass off as a sentence. Calibrated
# between the two anchors the spec pins: the held-out protocol dump whose
# whole survivor is a handful of words (<= 8 per line, must abstain) and a
# real customer question pasted under five mail-header lines (12 words, must
# vote). 9 sits with margin on both sides; the absolute MIN_PROSE_WORDS floor
# below keeps its separate job of rejecting genuinely short prose.
MIN_PROSE_SENTENCE_WORDS = 9

# After locking, the FIRST switch needs this many consecutive STRONG
# non-abstaining turns in the new language (1 = the triggering turn itself
# switches; a weak vote cannot switch at any point, see _turn_is_strong_switch).
# Abstaining turns are skipped entirely and never break the streak; weak
# counter-votes are inert; only a vote for another language or a return to
# the locked language breaks a pending streak.
FIRST_SWITCH_CONFIRMATIONS = 1

# Every switch AFTER the first needs this many consecutive strong
# non-abstaining turns. Raising the cost keeps a conversation from
# flip-flopping on incidental stimuli; switching back to a just-left language
# costs the same as switching to a brand-new one (no cheap ping-pong). An
# explicit language request resets the cost back to FIRST_SWITCH_CONFIRMATIONS.
SUBSEQUENT_SWITCH_CONFIRMATIONS = 2

# Step 2: a turn longer than this is treated as prose that mentions a
# language, not as a request to answer in one. Requests are short by nature.
MAX_EXPLICIT_REQUEST_WORDS = 12

# Machine-readable reasons on LanguageDecision (telemetry vocabulary).
REASON_ESTABLISHED = "established"
REASON_SWITCHED = "switched"
REASON_LOCKED = "locked"
REASON_EXPLICIT_REQUEST = "explicit_request"
REASON_NO_EVIDENCE = "no_evidence"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_DETECTOR_UNAVAILABLE = "detector_unavailable"

# Mechanism vocabulary on LanguageDecision.method: WHICH step identified the
# turn that set the current language. A reason of "established" alone cannot
# tell an explicit request apart from a function-word decision or a langid
# vote; the method can.
METHOD_EXPLICIT_REQUEST = "explicit_request"
METHOD_FUNCTION_WORDS = "function_words"
METHOD_LANGID = "langid"

# TurnEvidence classifications.
EVIDENCE_PROSE = "prose"  # enough prose survived: the turn may cast a vote
EVIDENCE_MACHINE = "machine"  # machine-dominated or too little prose: abstain
EVIDENCE_EMPTY = "empty"  # nothing but whitespace in, abstain

# Language names and endonyms, keyed by the TARGET language they denote.
# Each of the six targets is expressed in each of the six languages (plus
# accent-free variants, because keyboard-less users type "frances" for
# "français"). Values are matched lowercased as whole word tokens.
_LANGUAGE_NAMES_BY_TARGET: dict[str, tuple[str, ...]] = {
    "nl": (
        "nederlands",  # nl, en
        "nederlandse",  # nl (adjectief: "het Nederlandse")
        "dutch",  # en
        "niederländisch",  # de
        "niederlandisch",  # de, accent-free typo
        "néerlandais",  # fr
        "neerlandais",  # fr, accent-free typo
        "holandês",  # pt
        "neerlandés",  # es
        "holandés",  # es variant
        "holandes",  # pt/es accent-free
        "neerlandes",  # es accent-free
    ),
    "en": (
        "engels",  # nl
        "english",  # en
        "englisch",  # de
        "anglais",  # fr
        "inglês",  # pt
        "ingles",  # pt/es accent-free
        "inglés",  # es
    ),
    "de": (
        "duits",  # nl
        "german",  # en
        "deutsch",  # de
        "allemand",  # fr
        "alemão",  # pt
        "alemao",  # pt accent-free
        "alemán",  # es
        "aleman",  # es accent-free
    ),
    "fr": (
        "frans",  # nl
        "french",  # en
        "französisch",  # de
        "franzosisch",  # de accent-free
        "français",  # fr
        "francais",  # fr accent-free
        "francês",  # pt
        "frances",  # pt/es accent-free
        "francés",  # es
    ),
    "pt": (
        "portugees",  # nl
        "portuguese",  # en
        "portugiesisch",  # de
        "portugais",  # fr
        "português",  # pt
        "portugues",  # pt/es accent-free
        "portugués",  # es
    ),
    "es": (
        "spaans",  # nl
        "spanish",  # en
        "spanisch",  # de
        "espagnol",  # fr
        "espanhol",  # pt
        "español",  # es
        "espanol",  # es accent-free
        "castellano",  # es synonym
        "castellaans",  # nl synonym
    ),
}

EXPLICIT_LANGUAGE_NAMES: dict[str, str] = {
    name: target for target, names in _LANGUAGE_NAMES_BY_TARGET.items() for name in names
}

# Words that carry no content next to a bare language name, so "Spaans
# a.u.b." or "Nederlands graag" still reads as a request: politeness and
# filler only. Request VERBS are deliberately absent — a verb anywhere in a
# sentence is not a request ("onze klant is Portugees, kun je dat uitzoeken?"),
# which is why the directing-preposition rule below decides the normal case.
_REQUEST_FILLER_WORDS = frozenset(
    {
        "graag", "alstublieft", "alsjeblieft", "aub", "svp", "dank", "bedankt",  # nl
        "please", "thanks", "thank", "pls",  # en
        "bitte", "danke",  # de
        "merci",  # fr ("svp" is listed once, under nl; it is shared)
        "favor", "obrigado", "obrigada",  # pt
        "gracias",  # es
    }
)


# ---------------------------------------------------------------------------
# Evidence-gate line classification
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_TOKEN_RE = re.compile(r"\S+")

# Fenced code blocks (``` or ~~~). An unclosed fence swallows the rest of the
# turn: an unfinished paste is still a paste.
_FENCE_RE = re.compile(r"(?:```|~~~).*?(?:```|~~~|$)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
# Double-quoted passages: pasted mail text / foreign sentences in quotes are
# the classic weak stimulus that must not hijack the conversation language.
_QUOTED_SPAN_RE = re.compile(r'"[^"\n]*"')

# Quoting detection for the switch-strength rule (step 3): a turn that wraps
# ANY span in quotes is quoting, not talking. Covers straight and typographic
# quotes. A lone straight single quote is deliberately NOT treated as a span:
# in French and English it is a typewriter apostrophe (s'il, don't), and matching '…'
# would flag ordinary prose as quoting.
_QUOTED_SWITCH_RE = re.compile(
    '"[^"\\n]*"'
    '|\u201c[^\u201d]*\u201d'
    '|\u2018[^\u2019]*\u2019'
    '|\u00ab[^\u00bb]*\u00bb'
)

# Protocol/mail header: token-ish key + ":" + non-empty value. Keys contain
# no spaces (From:, Content-Type:, X-Mailer:) — a space rules it out so prose
# like "Let op: antwoord in het Nederlands" keeps its vote.
_HEADER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._+-]*:[ \t]*\S")
_TIMESTAMP_LINE_RE = re.compile(
    r"""^["'`\[]?\s*
    (?:
        \d{4}-\d{2}-\d{2}                 # ISO-8601 date / datetime
      | \d{1,2}[./-]\d{1,2}[./-]\d{2,4}   # numeric date
      | \d{1,2}:\d{2}(?::\d{2})?          # clock time
      | \d{10,13}                         # unix epoch seconds/millis
    )
""",
    re.VERBOSE,
)
# NOTE: quoted spans are stripped before line classification, so the Python
# traceback form appears as `File , line 42, in main` here — patterns must not
# depend on the filename quotes still being present.
_STACK_FRAME_RE = re.compile(
    r"^at\s+[\w.$<>[\]]+\("  # Java / JS
    r"|^File\b.*\bline\s+\d+"  # Python traceback frame
    r"|^Traceback\s+\(most recent call last\)"
    r"|^raise\s+\w[\w.]*(?:\(|:)"  # Python raise frame
    r"|^During handling of the above exception"
    r"|^#[0-9]+\s+0x[0-9a-fA-F]+"  # gdb frame
    r"|^in\s+\S+\s+at\s+0x[0-9a-fA-F]+"  # node frame
    r"|^.*,.*\bline\s+\d+\b"  # traceback tail on wrapped lines
)
_TABLE_ROW_RE = re.compile(r"^\|")
_URL_LINE_RE = re.compile(r"^<?(?:https?://|ftp://|mailto:|www\.)\S+>?$", re.IGNORECASE)
_JSON_OR_TAG_START_RE = re.compile(r"^[{}\[\]<>]")
_JSON_KV_RE = re.compile(r'"\s*[A-Za-z_][\w.-]*\s*"\s*:')
_LOG_LINE_RE = re.compile(
    r"^\[?(?:DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL|FATAL|TRACE)\]?(?::\s|\s|$)"
)
# Token characters that may surround an identifier without being part of it.
_STRIP_PUNCTUATION = "()[]{}<>,;:!?'\"\\|/"


def _is_character_soup(line: str) -> bool:
    """True when the line is mostly punctuation, digits or hex noise."""
    chars = [c for c in line if not c.isspace()]
    if not chars:
        return True
    letters = sum(1 for c in chars if c.isalpha())
    digits = sum(1 for c in chars if c.isdigit())
    return letters / len(chars) < 0.45 or digits / len(chars) > 0.40


def _token_is_identifier_like(token: str) -> bool:
    """True for code identifiers: underscores, dotted names, letter+digit mixes."""
    t = token.strip(_STRIP_PUNCTUATION)
    if not t:
        return False
    if "_" in t and any(c.isalpha() for c in t):
        return True
    parts = t.split(".")
    if (
        len(parts) >= 2
        and all(parts)
        and any(c.isalpha() for c in t)
        and all(p.isascii() and p.isalnum() for p in parts)
    ):
        return True
    return len(t) >= 5 and any(c.isdigit() for c in t) and any(c.isalpha() for c in t)


def _is_identifier_dominated(line: str) -> bool:
    """True when most tokens look like identifiers rather than words."""
    tokens = _TOKEN_RE.findall(line)
    if not tokens:
        return True
    idish = sum(1 for tok in tokens if _token_is_identifier_like(tok))
    return idish > 0 and idish * 2 >= len(tokens)


def _token_is_machine_identifier(token: str) -> bool:
    """True when ``token`` names a technical identifier rather than a word.

    Extends the gate's :func:`_token_is_identifier_like` (underscored names,
    dotted names, long letter+digit mixes) with the hyphenated multi-part names
    the line-level gate never needs to see: a dash-joined token counts only when
    it also carries an uppercase letter or a digit (``X-Error-Code``,
    ``ordernummer-2``) or is built from three or more parts
    (``upstream-payment-gateway``), so ordinary hyphenated words in the target
    languages — "puis-je", "well-known", "e-mail" — remain clean prose.
    """
    if _token_is_identifier_like(token):
        return True
    t = token.strip(_STRIP_PUNCTUATION)
    if "-" in t:
        parts = t.split("-")
        if all(parts) and (
            any(c.isupper() for c in t)
            or any(c.isdigit() for c in t)
            or len(parts) >= 3
        ):
            return True
    return False


def _turn_is_strong_switch(text: str) -> bool:
    """Whether a voting turn may single-handedly switch the language (step 3).

    The rule is TALKING versus QUOTING, not quantity. A turn is strong only
    when it wraps nothing in quotes and names no technical identifier: a
    quoted span or an identifier-shaped token means the user is discussing
    machine output — a log line, an error string, a field name — which is weak
    evidence of a language change however clean the surrounding sentence is. A
    bare number is not an identifier ("waar komt die 404 vandaan?" stays
    strong). The check runs on the RAW turn, before the gate strips quotes and
    machine lines, so pasted material still counts against the turn.
    """
    if _QUOTED_SWITCH_RE.search(text):
        return False
    return not any(_token_is_machine_identifier(tok) for tok in _TOKEN_RE.findall(text))


# Sentence words for the proportional rule's rescue measure: whitespace-
# separated tokens that are actual words. Internal hyphens and apostrophes
# stay inside a word ("puis-je", "factuur-nummer", "don't"); surrounding
# punctuation is stripped; an embedded "=", ":", ",", "/" or digit is not a
# word. This is deliberately NOT _WORD_RE: that regex counts every letter
# run, so one dense protocol attribute line ("rcvr-rtt=all:10000 ...")
# out-words a real sentence and silently defeats the rescue exception.
_SENTENCE_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*", re.UNICODE)


def _sentence_word_count(line: str) -> int:
    """Count whitespace-separated word tokens in one surviving line."""
    return sum(
        1
        for token in _TOKEN_RE.findall(line)
        if _SENTENCE_WORD_RE.fullmatch(token.strip(_STRIP_PUNCTUATION))
    )


def _line_is_machine(line: str) -> bool:
    """Classify one whitespace-stripped survivor line as machine-like or not."""
    if _URL_LINE_RE.match(line):
        return True
    if _TABLE_ROW_RE.match(line):
        return True
    if _JSON_OR_TAG_START_RE.match(line):
        return True
    if _JSON_KV_RE.search(line):
        return True
    if _LOG_LINE_RE.match(line):
        return True
    if _HEADER_LINE_RE.match(line):
        return True
    if _TIMESTAMP_LINE_RE.match(line):
        return True
    if _STACK_FRAME_RE.match(line):
        return True
    return _is_character_soup(line) or _is_identifier_dominated(line)


@dataclass(frozen=True)
class TurnEvidence:
    """Result of the evidence gate for a single user turn.

    ``classification`` is one of :data:`EVIDENCE_PROSE` (the turn may cast a
    vote), :data:`EVIDENCE_MACHINE` (machine-dominated or too little prose
    survived) or :data:`EVIDENCE_EMPTY` (no text at all). ``prose`` is the
    surviving prose — the exact string later steps would judge; empty when
    nothing survived.
    """

    classification: str
    prose: str

    @property
    def has_evidence(self) -> bool:
        return self.classification == EVIDENCE_PROSE


def classify_turn_evidence(text: str) -> TurnEvidence:
    """Decide whether a user turn carries linguistic evidence at all.

    Runs BEFORE language identification (step 1 of the spec): machine text
    frequently contains real words, so an identifier will confidently label
    it, and that label must never count. Strips fenced code blocks, inline
    code spans and quoted passages, then classifies every remaining
    non-empty line as machine-like (protocol/mail headers, timestamps,
    stack frames, table rows, log lines, bare URLs, JSON/XML,
    punctuation/digit/hex soup, identifier-dominated) or as prose.

    The turn abstains (:data:`EVIDENCE_MACHINE`) when too little prose
    survives, measured two independent ways:

    * absolutely — fewer than :data:`MIN_PROSE_WORDS` word tokens survive
      (the floor for genuinely short prose turns);
    * proportionally — when a clear majority of the lines are machine-like
      and no single surviving line reaches :data:`MIN_PROSE_SENTENCE_WORDS`
      sentence words (see :func:`_sentence_word_count` — space-separated
      words, not letter runs inside attribute syntax), the readable
      leftovers are fragments of an otherwise mechanical paste, not prose,
      however many of them clear the absolute floor. Without this rule a
      protocol dump (status line + headers + identifier soup) votes whenever
      the identifier happens to be confident about the debris — gate
      decisions must not depend on the identifier at all.

    Note that :func:`resolve_conversation_language` still checks short
    surviving prose against the explicit-request table (step 2 is exempt
    from the floor), while :meth:`has_evidence` reports whether the turn can
    be language-judged at all.
    """
    if not text or not text.strip():
        return TurnEvidence(EVIDENCE_EMPTY, "")

    cleaned = _FENCE_RE.sub(" ", text)
    cleaned = _INLINE_CODE_RE.sub(" ", cleaned)
    cleaned = _QUOTED_SPAN_RE.sub(" ", cleaned)

    prose_lines: list[str] = []
    total_lines = 0
    machine_lines = 0
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        total_lines += 1
        if line.startswith(">"):  # quoted block (mail-style quote)
            machine_lines += 1
            continue
        if _line_is_machine(line):
            machine_lines += 1
            continue
        prose_lines.append(line)

    prose = "\n".join(prose_lines)
    prose_words = len(_WORD_RE.findall(prose))

    machine_majority = total_lines > 0 and machine_lines * 2 > total_lines
    if machine_majority and not any(
        _sentence_word_count(line) >= MIN_PROSE_SENTENCE_WORDS for line in prose_lines
    ):
        return TurnEvidence(EVIDENCE_MACHINE, prose)
    if prose_words >= MIN_PROSE_WORDS:
        return TurnEvidence(EVIDENCE_PROSE, prose)
    return TurnEvidence(EVIDENCE_MACHINE, prose)


# ---------------------------------------------------------------------------
# Explicit language requests
# ---------------------------------------------------------------------------


# A language name only signals a REQUEST when a directing preposition governs
# it. Without this, any polite verb elsewhere in the sentence turns a remark
# ABOUT a language into a request to speak it.
_REQUEST_DIRECTING_WORDS = frozenset(
    {
        "in", "into", "to", "naar", "op",          # nl / en
        "auf", "ins",                               # de
        "en", "dans",                               # fr / es
        "em", "no", "na", "para",                   # pt
    }
)

# Articles that may sit between the preposition and the language name
# ("in HET nederlands", "auf DEM Deutsch", "en LA lengua espanola").
_REQUEST_SKIPPABLE_ARTICLES = frozenset(
    {"het", "de", "den", "dem", "der", "die", "das", "la", "le", "el", "lo", "o", "a", "the"}
)


def detect_explicit_language_request(prose: str) -> str | None:
    """Return the requested target code when ``prose`` IS the request.

    Matches language names and endonyms from :data:`EXPLICIT_LANGUAGE_NAMES`.
    A turn qualifies only when it is short (<= :data:`MAX_EXPLICIT_REQUEST_WORDS`
    words) and dominated by the request: exactly ONE distinct language name
    appears, and either the whole turn is at most 2 words ("Duits?",
    "In English") or a request verb/preposition from the hint table is present
    ("In English please", "Auf Deutsch antworten"). A long analytical paragraph
    that merely mentions a language name is not a request. Two different names
    ("translate English to Dutch") are ambiguous and also not a request.
    """
    words = [w.lower() for w in _WORD_RE.findall(prose)]
    if not words or len(words) > MAX_EXPLICIT_REQUEST_WORDS:
        return None

    langs = {EXPLICIT_LANGUAGE_NAMES[w] for w in words if w in EXPLICIT_LANGUAGE_NAMES}
    if len(langs) != 1:
        return None
    target = next(iter(langs))

    if len(words) <= 2:
        return target
    # A hint word ANYWHERE in the turn is not enough: "onze klant is Portugees,
    # kun je dat uitzoeken?" is a Dutch sentence ABOUT Portuguese, not a request
    # to speak it. The name must be governed by a directing preposition
    # ("in het nederlands", "auf Deutsch", "en francais"), optionally across one
    # article, so the name is the OBJECT of the request rather than its subject.
    # A bare name plus politeness or filler is still a request ("Spaans a.u.b.",
    # "Nederlands graag"): nothing in the turn carries meaning beyond the name.
    if not [
        w
        for w in words
        if len(w) > 1
        and w not in EXPLICIT_LANGUAGE_NAMES
        and w not in _REQUEST_SKIPPABLE_ARTICLES
        and w not in _REQUEST_FILLER_WORDS
    ]:
        return target

    for index, word in enumerate(words):
        if EXPLICIT_LANGUAGE_NAMES.get(word) != target:
            continue
        for back in (1, 2):
            if index - back < 0:
                break
            preceding = words[index - back]
            if preceding in _REQUEST_DIRECTING_WORDS:
                return target
            if preceding not in _REQUEST_SKIPPABLE_ARTICLES:
                break
    return None


# ---------------------------------------------------------------------------
# langid (lazy, restricted, never fatal)
# ---------------------------------------------------------------------------

# Built once, lazily: loading/decoding the embedded model is expensive, so it
# must not happen at import time. _identifier_built distinguishes "not built
# yet" from "tried and langid is unavailable" (built=True, identifier=None).
_identifier: Any = None
_identifier_built = False


def _get_identifier() -> Any:
    """Return the shared restricted LanguageIdentifier, or None if unavailable.

    Never raises: an ImportError (no vendored ``langid``, broken numpy) or any
    model-loading failure degrades to ``None`` for the lifetime of the
    process, and callers abstain on turns that need identification.
    """
    global _identifier, _identifier_built
    if _identifier_built:
        return _identifier
    _identifier_built = True
    try:
        from langid.langid import LanguageIdentifier, model

        identifier = LanguageIdentifier.from_modelstring(model, norm_probs=True)
        identifier.set_languages(list(TARGET_LANGUAGES))
        _identifier = identifier
    except Exception:  # noqa: BLE001, RUF100 - degrade on ANY load failure
        _identifier = None
    return _identifier


def _reset_detector_cache() -> None:
    """Drop the lazily built identifier (test seam for degradation tests)."""
    global _identifier, _identifier_built
    _identifier = None
    _identifier_built = False


# Protocol/product acronyms Klai's Dutch support traffic asks about
# constantly. langid's confidence in the surrounding sentence is unchanged by
# their presence (measured), but the acronym still counts as a word toward
# the LONG_PROSE_WORDS tier switch, which drops the confidence bar required
# to vote from IDENTIFY_MIN_CONFIDENCE_SHORT (0.99) to IDENTIFY_MIN_CONFIDENCE
# (0.70) — enough for a borderline-English score to wrongly pass.
_PROTOCOL_ACRONYMS = frozenset(
    {
        "tcp", "ip", "dns", "sip", "voip", "ssl", "dhcp", "nat", "api",
        "pbx", "udp", "tls", "http", "https",
    }
)
_PROTOCOL_ACRONYM_RE = re.compile(
    r"\b(?:" + "|".join(sorted(_PROTOCOL_ACRONYMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _strip_protocol_acronyms(text: str) -> str:
    """Blank out :data:`_PROTOCOL_ACRONYMS` tokens before identification."""
    return _PROTOCOL_ACRONYM_RE.sub(" ", text)


# Unambiguous function words for the short-prose rule. EXCLUSION RULE: a word
# only belongs in a table when the OTHER language cannot use it — words shared
# by Dutch and English (is, we, in, de, die, dat, was, had, "met" is English
# past tense too, and every single letter) carry no signal and are in NEITHER
# table, so they can never decide anything. Lowercased whole tokens.
_DUTCH_FUNCTION_WORDS = frozenset(
    {
        "wat", "welke", "waarom", "wanneer", "wie", "waar", "hoe",  # vragers
        "kan", "kun", "kunt", "kunnen", "wil", "willen", "zou", "zouden",
        "moet", "moeten", "heb", "heeft", "hebben",  # hulpwerkwoorden
        "ik", "mij", "mijn", "mezelf", "jij", "hij", "het",  # voornaamwoorden
        "een", "geen", "niet", "deze", "maar", "ook", "omdat",  # overig
    }
)
_ENGLISH_FUNCTION_WORDS = frozenset(
    {
        "what", "when", "where", "which", "who", "why", "how",
        "can", "could", "would", "should", "may", "might",
        "do", "does", "did", "have", "has", "are",
        "the", "this", "that", "these", "those", "there",
        "they", "them", "you", "your", "with", "about", "from",
    }
)


def _short_prose_function_language(text: str) -> str | None:
    """Decide short prose from unambiguous function words; None = no signal.

    Short surviving prose (<= :data:`SHORT_PROSE_MAX_WORDS` words, measured
    after protocol-acronym stripping like the confidence tiers) carries a
    deterministic signal langid ignores: function words that exist in Dutch
    and not in English, or the reverse. When EXACTLY ONE side's words appear,
    that side is the language; words shared by both are in neither table.
    Mixed or markerless prose, and everything longer, returns None and falls
    through to the normal confidence-tiered identification. The evidence gate
    and the explicit-request table keep precedence: this runs inside the
    identification step only, on prose that already passed both.
    """
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if not words or len(words) > SHORT_PROSE_MAX_WORDS:
        return None
    seen = set(words)
    has_nl = bool(seen & _DUTCH_FUNCTION_WORDS)
    has_en = bool(seen & _ENGLISH_FUNCTION_WORDS)
    if has_nl == has_en:
        return None
    return "nl" if has_nl else "en"


def _identify(identifier: Any, text: str) -> tuple[str, str] | None:
    """Classify ``text``; return ``(target code, method)`` or None (abstain).

    ``method`` names the step that produced the code
    (:data:`METHOD_FUNCTION_WORDS` or :data:`METHOD_LANGID`) so callers can
    report HOW a vote was identified, not just what it was.

    Short nl/en prose: :func:`_short_prose_function_language` decides it
    deterministically from unambiguous function words whenever exactly one
    side's words appear, because langid's normalised confidence is unreliable
    at that length — it abstains on most of it and lands a hair under (or
    over) the bar on the rest.
    Confidence tiering: prose of :data:`LONG_PROSE_WORDS` words or more must
    reach :data:`IDENTIFY_MIN_CONFIDENCE`; shorter surviving prose must reach
    :data:`IDENTIFY_MIN_CONFIDENCE_SHORT`, because cross-language look-alike
    fragments fool langid at confidence values that look fine at 0.70.
    Protocol acronyms (see :data:`_PROTOCOL_ACRONYMS`) are stripped first, for
    the word count as well as for the text handed to langid.
    """
    stripped = _strip_protocol_acronyms(text)
    try:
        lang, confidence = identifier.classify(stripped)
    except Exception:  # noqa: BLE001, RUF100 - a broken classify is an abstention
        return None
    # The function-word rule only breaks the nl/en tie langid is unreliable
    # on; it never overrides a third language. German "wie" (how) sits in the
    # Dutch table as "wie" (who): "Wie funktioniert das?" must stay de.
    if lang in ("nl", "en"):
        deterministic = _short_prose_function_language(stripped)
        if deterministic is not None:
            return (deterministic, METHOD_FUNCTION_WORDS)
    words = len(_WORD_RE.findall(stripped))
    threshold = IDENTIFY_MIN_CONFIDENCE if words >= LONG_PROSE_WORDS else IDENTIFY_MIN_CONFIDENCE_SHORT
    if lang in TARGET_LANGUAGES and float(confidence) >= threshold:
        return (str(lang), METHOD_LANGID)
    return None


def identify_text_language(text: str) -> str | None:
    """Identify the language of ONE standalone text. Public single-text entry.

    Runs exactly the steps :func:`resolve_conversation_language` runs per user
    turn — evidence gate, explicit-request table, confidence-tiered
    identification — on a single text, for call sites that have a lone query
    (or an answer to measure) and no conversation to replay. Returns a target
    code, or ``None`` for "we do not know" (no prose, machine-dominated text,
    sub-threshold confidence, or langid unavailable); never raises.
    """
    evidence = classify_turn_evidence(text)
    requested = detect_explicit_language_request(evidence.prose)
    if requested is not None:
        return requested
    if not evidence.has_evidence:
        return None
    identifier = _get_identifier()  # cached; repeated calls are cheap
    if identifier is None:
        return None
    identified = _identify(identifier, evidence.prose)
    return identified[0] if identified is not None else None


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _message_text(message: dict) -> str:
    """Extract text from an OpenAI chat message; content may be str or parts.

    Mirrors klai_kb_request_context.message_text, re-implemented to keep this
    module standalone (flat-vendored without sibling imports in the container).
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _iter_user_texts(messages: list[dict]) -> Iterator[str]:
    """Yield the text of every user turn, oldest to newest; ignore the rest.

    Tolerates malformed input (non-list argument, non-dict entries, missing
    keys): assistant turns, system prompts and anything unrecognised simply
    never vote.
    """
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        yield _message_text(message)


@dataclass(frozen=True)
class LanguageDecision:
    """Outcome of one conversation replay.

    ``language`` is the decided ISO-639-1 code from :data:`TARGET_LANGUAGES`,
    or ``None`` meaning "we do not know" — a legitimate, common outcome; the
    caller should then fall back to model-side detection.

    ``reason`` (machine-readable, for telemetry):
      * established  — language set, opening window not yet exhausted
      * locked       — window exhausted and the language survived to the end
                       without a post-window switch
      * switched     — the final language arrived via a confirmed post-window
                       switch
      * explicit_request — the last language change was an explicit request
      * no_evidence  — nothing to judge: no user turns, or every turn was
                       rejected by the evidence gate (machine-dominated
                       lines or too little surviving prose). The gate makes
                       this call without consulting the identifier, so
                       machine text lands here and never in low_confidence.
      * low_confidence — some turn passed the gate as prose but
                       identification stayed below the confidence threshold
                       and never established a language
      * detector_unavailable — some turn needed identification but langid
                       could not be loaded

    ``votes`` counts user turns that actually carried a vote (identified or
    explicit request); ``abstentions`` counts the user turns that carried
    none; ``votes + abstentions`` equals the number of user turns.
    ``switches`` counts every conversation-language change during the replay
    (window moves, confirmed switches and explicit requests). ``locked`` is
    True once the opening window was exhausted (``votes >=
    OPENING_WINDOW_TURNS``).
    ``method`` names the mechanism that identified the turn which SET the
    current language — :data:`METHOD_EXPLICIT_REQUEST`,
    :data:`METHOD_FUNCTION_WORDS` or :data:`METHOD_LANGID`, and ``None``
    when no language was set. A reinforcing turn (same language, no switch)
    does not overwrite it; the method keeps naming the turn that decided.
    """

    language: str | None
    reason: str
    votes: int = 0
    abstentions: int = 0
    switches: int = 0
    locked: bool = False
    method: str | None = None


@dataclass
class _ReplayState:
    """Mutable hysteresis state of one replay (private to resolve)."""

    language: str | None = None
    votes: int = 0
    abstentions: int = 0
    switches: int = 0
    pending_language: str | None = None  # candidate of the current switch streak
    pending_run: int = 0
    switch_cost: int = FIRST_SWITCH_CONFIRMATIONS
    last_set: str = ""  # "", "establish", "window", "switch" or "explicit"
    last_method: str | None = None  # mechanism of the turn that set last_set

    def cast(self, lang: str, *, explicit: bool, strong: bool, method: str) -> None:
        """Apply one voting turn (gate passed and language determined).

        ``strong`` is the turn's strength for SWITCHING purposes: it wraps
        nothing in quotes and names no technical identifier
        (:func:`_turn_is_strong_switch`; explicit requests pass ``strong=True``
        as a formality — step 2 ignores turn strength entirely). ``method`` is
        the mechanism that identified this turn (:data:`METHOD_LANGID`,
        :data:`METHOD_FUNCTION_WORDS` or :data:`METHOD_EXPLICIT_REQUEST`); it
        is recorded whenever the turn sets the conversation language, so a
        reinforcing turn leaves the deciding turn's method alone. Establishing
        when nothing is set is cheap and accepts weak turns; changing an
        already established language never is: inside the opening window it
        costs one strong turn, outside it a strong turn plus the
        confirmation streak. A weak vote against the established language is
        inert — it cannot switch and cannot advance or break a switch
        streak, exactly like a paste would be if it had failed the gate
        outright; unlike an abstention it still counts as a vote.
        """
        self.votes += 1
        if explicit:
            # Step 2: immediate switch, confirmation streak cleared, cost reset.
            if self.language is not None and lang != self.language:
                self.switches += 1
            self.language = lang
            self.last_set = "explicit"
            self.last_method = method
            self.pending_language = None
            self.pending_run = 0
            self.switch_cost = FIRST_SWITCH_CONFIRMATIONS
            return

        if self.language is None:
            self.language = lang
            self.last_set = "establish"
            self.last_method = method
        elif lang == self.language:
            # Reinforcing the established language: break any pending switch
            # streak; inside the window also mark the turn as a window move.
            if self.votes <= OPENING_WINDOW_TURNS:
                self.last_set = "window"
            self.pending_language = None
            self.pending_run = 0
        elif not strong:
            # Switching is never free: a turn that quotes a span or names a
            # technical identifier is discussing machine output, not asking to
            # change language — too weak to overturn an established language,
            # at any point in the conversation, opening window included.
            return
        elif self.votes <= OPENING_WINDOW_TURNS:
            # Inside the opening window: one strong turn wins — no confirmation
            # streak, but the talking-vs-quoting strength bar still applies.
            self.switches += 1
            self.language = lang
            self.last_set = "window"
            self.last_method = method
        else:
            self._advance_streak(lang, method)

    def _advance_streak(self, lang: str, method: str) -> None:
        """Post-window switch confirmation for a vote against the lock."""
        if lang == self.pending_language:
            self.pending_run += 1
        else:
            self.pending_language = lang
            self.pending_run = 1
        if self.pending_run >= self.switch_cost:
            self.switches += 1
            self.language = lang
            self.last_set = "switch"
            self.last_method = method
            self.switch_cost = SUBSEQUENT_SWITCH_CONFIRMATIONS
            self.pending_language = None
            self.pending_run = 0



def _decision_reason(
    state: _ReplayState,
    *,
    locked: bool,
    low_confidence_seen: bool,
    detector_needed_but_unavailable: bool,
) -> str:
    if state.language is None:
        if detector_needed_but_unavailable:
            return REASON_DETECTOR_UNAVAILABLE
        if low_confidence_seen:
            return REASON_LOW_CONFIDENCE
        return REASON_NO_EVIDENCE
    if state.last_set == "explicit":
        return REASON_EXPLICIT_REQUEST
    if state.last_set == "switch":
        return REASON_SWITCHED
    return REASON_LOCKED if locked else REASON_ESTABLISHED


def resolve_conversation_language(messages: list[dict]) -> LanguageDecision:
    """Derive the conversation language by replaying the user turns in order.

    Pure and deterministic. Walks user turns oldest-to-newest: abstaining
    turns (failed evidence gate or sub-threshold confidence) are skipped
    entirely and can never set, switch or reset anything. Explicit language
    requests switch immediately and reset the switch cost. Switching an
    already established language always costs more evidence than
    establishing: inside the opening window it needs one strong turn (a turn
    that quotes nothing and names no technical identifier, see
    :func:`_turn_is_strong_switch`), after the window a strong turn plus
    :data:`FIRST_SWITCH_CONFIRMATIONS` (first switch) /
    :data:`SUBSEQUENT_SWITCH_CONFIRMATIONS` (every later) consecutive
    non-abstaining turns in the new language. Never raises; with langid
    unavailable only explicit requests can still produce a language.
    """
    # None = not fetched yet, or fetched and unavailable; _get_identifier()
    # distinguishes the two internally, so calling it repeatedly is cheap.
    identifier: Any = None
    state = _ReplayState()
    low_confidence_seen = False
    detector_needed_but_unavailable = False

    for text in _iter_user_texts(messages):
        evidence = classify_turn_evidence(text)
        if evidence.classification == EVIDENCE_EMPTY:
            state.abstentions += 1
            continue

        # Step 2 outranks the gate's prose floor: requests are short by nature,
        # so the request table is consulted even when the turn is too little
        # prose to *judge a language* by ("Duits?" votes; "Ja." does not).
        requested = detect_explicit_language_request(evidence.prose)
        if requested is not None:
            # Step 2 ignores turn strength entirely; strong=True is a
            # formality so the cast signature stays uniform.
            state.cast(
                requested, explicit=True, strong=True, method=METHOD_EXPLICIT_REQUEST
            )
            continue

        if not evidence.has_evidence:
            state.abstentions += 1
            continue

        if identifier is None:
            identifier = _get_identifier()  # cached; repeated calls are cheap
        if identifier is None:
            detector_needed_but_unavailable = True
            state.abstentions += 1
            continue

        identified = _identify(identifier, evidence.prose)
        if identified is None:
            low_confidence_seen = True
            state.abstentions += 1
            continue

        # Step 3: a vote may change an established language only when the user
        # is talking rather than quoting — no quoted span, no machine identifier
        # anywhere in the raw turn.
        strong = _turn_is_strong_switch(text)
        state.cast(identified[0], explicit=False, strong=strong, method=identified[1])

    locked = state.language is not None and state.votes >= OPENING_WINDOW_TURNS
    return LanguageDecision(
        language=state.language,
        reason=_decision_reason(
            state,
            locked=locked,
            low_confidence_seen=low_confidence_seen,
            detector_needed_but_unavailable=detector_needed_but_unavailable,
        ),
        votes=state.votes,
        abstentions=state.abstentions,
        switches=state.switches,
        locked=locked,
        method=state.last_method,
    )
