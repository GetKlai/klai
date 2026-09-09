"""Tests for klai_chat_prompts.language — the conversation-level language
decision module (replacement for the per-message stopword counter).

The same suite runs against the vendored flat copy in
``deploy/litellm/tests/test_conversation_language.py``; this file pins the
canonical module to the written specification: evidence gate, explicit
requests, tiered identification, hysteresis replay, message shapes,
malformed input and langid degradation.
"""

from __future__ import annotations

import copy
import sys

import pytest

from klai_chat_prompts import language as clm

# ---------------------------------------------------------------------------
# Fixtures: language-bearing sentences and machine-text blocks
# ---------------------------------------------------------------------------

NL_1 = "Hoe kan ik een nieuwe gebruiker uitnodigen in ons team?"
NL_2 = "Kunt u mij ook uitleggen hoe de facturatie maandelijks wordt berekend?"
NL_3 = "Graag wil ik daarnaast weten waar ik de vorige facturen terugvind in de portal."
EN_1 = "How do I invite a new user to my team and set their permissions?"
EN_2 = "Could you also explain how the monthly billing and invoices are calculated here?"
EN_3 = "I would like to know where I can find the previous invoices in this portal."
DE_1 = "Wie kann ich einen neuen Benutzer in mein Team einladen und seine Rechte ändern?"
FR_1 = "Comment puis-je inviter un nouvel utilisateur dans mon équipe et modifier ses droits ?"
ES_1 = "¿Cómo puedo invitar a un nuevo usuario a mi equipo y cambiar sus permisos?"
PT_1 = "Como posso convidar um novo usuário para a minha equipe e alterar as permissões dele?"

FENCED = "```\nprint('this English text inside a fenced block must never vote at all')\n```"
JSON_STIMULUS = (
    '{"status": "error", "code": 500, "detail": "internal server failure on the primary host"}'
)
MAIL_HEADERS = (
    "From: jan@example.nl\n"
    "To: admin@klai.nl\n"
    "Subject: Re: Offerte nummer 12\n"
    "Date: 2024-03-01T14:02:33+01:00\n"
    "MIME-Version: 1.0"
)

# Correction 2 fixtures: protocol/packet dumps where a clear majority of the
# lines are machine-like (status line, header lines, identifier soup) and only
# fragments survive stripping. The fragments clear the absolute MIN_PROSE_WORDS
# floor — and the identifier labels readable English debris from them with
# near-total confidence — so the gate must reject them on proportion alone.
PROTOCOL_DUMP = (
    "HTTP/1.1 503 Service Unavailable\n"
    "X-Request-Id: 9f2c1e0a-77bd-4a11\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "upstream-pool-7: exhausted waiting for database hosts"
)
PACKET_DUMP = (
    "HTTP/1.1 200 OK\n"
    "GET /api/v2/orders?customer_id=4821&sort=created_at\n"
    "Host: klai.example.com\n"
    "Content-Type: application/json; charset=utf-8\n"
    "X-Trace-Id: a3f9c21d4b\n"
    "timeout waiting for issuer response\n"
    "retry the request later or contact the operator"
)

# Correction 1 fixture: a genuine vote (passes the gate and the identifier at
# full confidence in English) that is WEAK for switching purposes — its first
# line names a technical identifier (an error-code header and a hyphenated,
# digit-bearing slug), so the user is discussing machine output rather than
# asking to change language. The clean sentence on the second line cannot
# single-handedly overturn an established language.
WEAK_SWITCH_STIMULUS = (
    "X-Error-Detail-Code: 502 upstream-payment-gateway-refused-mandate-"
    "after-3-retries-attempt-batch-9\n"
    "The payment gateway refused the direct debit again"
)


def user_turns(*texts: str) -> list[dict]:
    return [{"role": "user", "content": text} for text in texts]


@pytest.fixture
def detector_unavailable(monkeypatch: pytest.MonkeyPatch):
    """Hide langid behind an ImportError and reset the module's lazy cache."""
    monkeypatch.setitem(sys.modules, "langid", None)
    monkeypatch.setitem(sys.modules, "langid.langid", None)
    clm._reset_detector_cache()
    yield
    # Re-enable normal building for every later test in this process.
    clm._reset_detector_cache()


# ---------------------------------------------------------------------------
# Step 1: the evidence gate
# ---------------------------------------------------------------------------


def test_gate_accepts_plain_prose() -> None:
    ev = clm.classify_turn_evidence(NL_1)
    assert ev.classification == clm.EVIDENCE_PROSE
    assert ev.has_evidence
    assert ev.prose == NL_1


def test_gate_keeps_prose_and_drops_headers_from_mixed_turn() -> None:
    body = "Ik begrijp de berekening van deze factuur niet, kunt u dat uitleggen?"
    ev = clm.classify_turn_evidence(f"{MAIL_HEADERS}\n\n{body}")
    assert ev.classification == clm.EVIDENCE_PROSE
    assert ev.prose == body


def test_gate_abstains_on_mail_and_protocol_headers() -> None:
    ev = clm.classify_turn_evidence(MAIL_HEADERS)
    assert ev.classification == clm.EVIDENCE_MACHINE
    assert not ev.has_evidence
    assert ev.prose == ""


def test_gate_abstains_on_fenced_code_block() -> None:
    assert not clm.classify_turn_evidence(FENCED).has_evidence


def test_gate_abstains_on_unclosed_fence() -> None:
    turn = "Kijk: ```\nimport os\nprint('hello there my friend')"
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_inline_code() -> None:
    turn = "Probeer dit: `please restart the entire service and clear the cache now`"
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_quoted_block() -> None:
    turn = "> Hello, I would like to cancel my subscription please\n> thanks a lot"
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_quoted_span() -> None:
    turn = '"Please cancel my subscription and refund everything immediately"'
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_stack_trace() -> None:
    turn = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 42, in main\n'
        "    raise ValueError('boom')\n"
        "ValueError: boom while connecting to the billing host"
    )
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_log_lines() -> None:
    turn = (
        "[2024-03-01 12:00:01] WARNING Connection pool exhausted by the request handler\n"
        "ERROR timeout while connecting database host"
    )
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_timestamped_lines() -> None:
    turn = (
        "2024-03-01 connection restored after failover\n"
        "14:02:33 heartbeat sent to worker pool\n"
        "3-1-2024 agenda note for the meeting room"
    )
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_table_rows() -> None:
    turn = "| Naam | Prijs |\n|---|---|\n| Acme BV | 1.234,56 |"
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_json_fragment() -> None:
    turn = JSON_STIMULUS + '\n"user_id": 42,\n"created": "2024-03-01"\n<entry key="a">'
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_bare_url() -> None:
    turn = "https://support.example.com/articles/how-to-reset-your-password-immediately"
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_punctuation_digit_hex_soup() -> None:
    turn = "0x7fff2a3b :: 88:19:ab:cd\n#### **** ---- (((("
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_on_identifier_dominated_lines() -> None:
    turn = "klai_kb_chat_mode.py check_klai_pii_map_store migration_2024_03.sql voys.klai.app"
    assert not clm.classify_turn_evidence(turn).has_evidence


def test_gate_abstains_when_too_little_prose_survives() -> None:
    assert clm.classify_turn_evidence("Dank je").classification == clm.EVIDENCE_MACHINE
    assert clm.classify_turn_evidence("Ja.").classification == clm.EVIDENCE_MACHINE


# --- Correction 2: the proportional (machine-majority) gate rule ----------


def test_gate_abstains_on_protocol_dump_with_fragment_survivors() -> None:
    # 3 of 4 lines are machine-like; the survivor "HTTP/1.1 503 Service
    # Unavailable" has exactly MIN_PROSE_WORDS word tokens, so the old
    # absolute floor let it through as PROSE and the decision depended on
    # the identifier happening to be unsure. The proportional rule rejects
    # it on structure alone, and the surviving fragment stays observable.
    ev = clm.classify_turn_evidence(PROTOCOL_DUMP)
    assert ev.classification == clm.EVIDENCE_MACHINE
    assert not ev.has_evidence
    assert ev.prose == "HTTP/1.1 503 Service Unavailable"
    # The turn abstains, so switch strength never comes into play here; the
    # identifier-shaped tokens it carries would have marked it weak anyway.
    assert not clm._turn_is_strong_switch(PROTOCOL_DUMP)


def test_gate_abstains_on_machine_majority_even_with_readable_fragments() -> None:
    # The identifier classifies these survivors as English at ~1.0
    # confidence; the gate must abstain anyway. A few readable lines
    # rescued from an otherwise mechanical paste are not prose.
    ev = clm.classify_turn_evidence(PACKET_DUMP)
    assert ev.classification == clm.EVIDENCE_MACHINE
    assert not ev.has_evidence
    assert "timeout waiting" in ev.prose  # what survived stays reportable


def test_gate_rescues_one_substantive_prose_line_under_machine_majority() -> None:
    # Boundary of MIN_PROSE_SENTENCE_WORDS (9): a header-dominated turn
    # votes only when a full-length user sentence survives intact.
    rescued = clm.classify_turn_evidence(
        "From: jan@example.nl\nTo: admin@klai.nl\n\n"
        "Waarom is het bedrag op deze factuur zo hoog"  # exactly 9 words
    )
    assert rescued.classification == clm.EVIDENCE_PROSE
    too_fragmentary = clm.classify_turn_evidence(
        "From: jan@example.nl\nTo: admin@klai.nl\n\n"
        "Waarom is het bedrag op deze factuur zo"  # 8 words
    )
    assert too_fragmentary.classification == clm.EVIDENCE_MACHINE


def test_gate_needs_a_majority_of_machine_lines_not_just_any_of_them() -> None:
    # Several short prose lines (every one under MIN_PROSE_SENTENCE_WORDS)
    # plus one machine line: no majority, so the proportional rule must not
    # fire — short real sentences still vote (the floor alone judges them).
    turn = (
        "Factuur-nummer: 2024-0081\n"
        "Ik begrijp dit bedrag niet.\n"
        "Kunt u dat uitleggen?\n"
        "Het klopt met niets."
    )
    ev = clm.classify_turn_evidence(turn)
    assert ev.classification == clm.EVIDENCE_PROSE


def test_gate_empty_text_is_classified_empty() -> None:
    ev = clm.classify_turn_evidence("   \n  \n")
    assert ev.classification == clm.EVIDENCE_EMPTY
    assert ev.prose == ""


def test_gate_surviving_prose_is_reported() -> None:
    # The gate exposes what survived so it can be tested independently of the
    # identifier; a quoted English sentence is gone, the Dutch frame remains.
    turn = 'Klant schreef "Can you please refund the full amount today" en wil een nieuw bedrag'
    ev = clm.classify_turn_evidence(turn)
    assert ev.has_evidence
    assert "refund" not in ev.prose
    assert " ".join(ev.prose.split()) == "Klant schreef en wil een nieuw bedrag"


# ---------------------------------------------------------------------------
# Step 2: explicit language requests
# ---------------------------------------------------------------------------


def test_explicit_request_dutch() -> None:
    assert clm.detect_explicit_language_request("Antwoord in het Nederlands") == "nl"
    assert clm.detect_explicit_language_request("Graag in het Nederlands") == "nl"


def test_explicit_request_english() -> None:
    assert clm.detect_explicit_language_request("Please reply in English") == "en"
    assert clm.detect_explicit_language_request("In English please") == "en"


def test_explicit_request_german() -> None:
    assert clm.detect_explicit_language_request("Auf Deutsch antworten, bitte") == "de"


def test_explicit_request_french() -> None:
    assert clm.detect_explicit_language_request("Réponds en français") == "fr"


def test_explicit_request_portuguese() -> None:
    assert clm.detect_explicit_language_request("Responda em português") == "pt"


def test_explicit_request_spanish() -> None:
    assert clm.detect_explicit_language_request("Puedes responder en español por favor") == "es"


def test_explicit_request_endonyms_across_languages() -> None:
    # Each language named in the OTHER target languages must resolve correctly.
    assert clm.detect_explicit_language_request("Hola, español") == "es"  # named in en/es
    assert clm.detect_explicit_language_request("Please Dutch") == "nl"  # English name, en words
    assert clm.detect_explicit_language_request("Niederländisch bitte") == "nl"  # de name for nl
    assert clm.detect_explicit_language_request("svp anglais") == "en"  # fr name for en
    assert clm.detect_explicit_language_request("franzosisch gerne") == "fr"  # accent-free de
    assert clm.detect_explicit_language_request("fale portugues") == "pt"  # accent-free pt
    assert clm.detect_explicit_language_request("parle allemant svp") is None  # misspelling


def test_explicit_request_bare_language_name_short_turn() -> None:
    assert clm.detect_explicit_language_request("Duits?") == "de"
    assert clm.detect_explicit_language_request("Spaans a.u.b.") == "es"


def test_statement_about_language_is_not_a_request() -> None:
    # Talking about learning a language is not asking to be answered in it.
    assert clm.detect_explicit_language_request("Ik leer Engels") is None


def test_two_language_names_are_ambiguous_not_request() -> None:
    turn = "Kun je dit vertalen van het Engels naar het Nederlands?"
    assert clm.detect_explicit_language_request(turn) is None


def test_long_paragraph_mentioning_language_is_not_request() -> None:
    paragraph = (
        "Voor de evaluatie van onze klantcorrespondentie willen we graag weten in "
        "hoeverre het Engels overheerst in de technische tickets en of wij dit "
        "rapport per afdeling opgesplitst moeten aanleveren voor de directie."
    )
    assert clm.detect_explicit_language_request(paragraph) is None


# ---------------------------------------------------------------------------
# Steps 4+5: replay, hysteresis, result
# ---------------------------------------------------------------------------


def test_no_user_turns_yields_no_evidence() -> None:
    decision = clm.resolve_conversation_language(
        [
            {"role": "system", "content": EN_1 + EN_2 + EN_3},
            {"role": "assistant", "content": EN_1 + EN_2 + EN_3},
        ]
    )
    assert decision.language is None
    assert decision.reason == clm.REASON_NO_EVIDENCE
    assert decision.votes == 0
    assert decision.abstentions == 0


def test_empty_messages_yields_no_evidence() -> None:
    decision = clm.resolve_conversation_language([])
    assert decision.language is None
    assert decision.reason == clm.REASON_NO_EVIDENCE


def test_first_clear_turn_establishes() -> None:
    decision = clm.resolve_conversation_language(user_turns(NL_1))
    assert decision.language == "nl"
    assert decision.reason == clm.REASON_ESTABLISHED
    assert decision.votes == 1
    assert decision.locked is False


def test_opening_window_follows_language_correction() -> None:
    # A user who opens in one language and corrects on the second turn with a
    # CLEAN sentence must be followed inside the window: one strong turn, no
    # confirmation streak. Strength is still required — see the weak-switch
    # tests below (a quoted or identifier-bearing turn never switches, even
    # during the opening window).
    decision = clm.resolve_conversation_language(user_turns(NL_1, EN_1))
    assert decision.language == "en"
    assert decision.switches == 1
    assert decision.reason == clm.REASON_ESTABLISHED

    third = clm.resolve_conversation_language(user_turns(NL_1, EN_1, FR_1))
    assert third.language == "fr"
    assert third.switches == 2
    assert third.locked is True
    assert third.reason == clm.REASON_LOCKED


# --- Switching costs a strong turn; strong means talking, not quoting ------


def test_weak_mixed_turn_does_not_switch_in_opening_window() -> None:
    # The old free-window rule let ANY identified language switch on turn 2,
    # so a pasted error log switched immediately. Under the corrected rule this
    # turn still votes (gate and confidence pass), but it is weak: it names a
    # technical identifier (an error-code header and a hyphenated slug).
    decision = clm.resolve_conversation_language(user_turns(NL_1, WEAK_SWITCH_STIMULUS))
    assert decision.language == "nl"
    assert decision.switches == 0
    assert decision.votes == 2  # it voted — it just may not switch
    assert decision.abstentions == 0
    assert decision.reason == clm.REASON_ESTABLISHED


def test_strong_mixed_turn_still_switches_in_opening_window() -> None:
    # A turn carrying an incidental stripped line but a clean, identifier-free,
    # quote-free sentence is strong: it switches inside the window as a clean
    # correction does. The stripped line has no identifier and no quotes, so
    # it costs the turn nothing under the talking-vs-quoting rule.
    turn = "ERROR the gateway is down\n" + EN_1
    decision = clm.resolve_conversation_language(user_turns(NL_1, turn))
    assert decision.language == "en"
    assert decision.switches == 1
    assert decision.votes == 2
    assert decision.abstentions == 0


def test_establishing_a_language_is_cheap_even_for_a_weak_turn() -> None:
    # Nothing established yet: the weak mixed turn may establish — the
    # strength bar prices switches, not establishments.
    decision = clm.resolve_conversation_language(user_turns(WEAK_SWITCH_STIMULUS))
    assert decision.language == "en"
    assert decision.reason == clm.REASON_ESTABLISHED
    assert decision.votes == 1
    assert decision.switches == 0


def test_weak_votes_never_switch_after_the_lock_either() -> None:
    # The corrected principle holds at every point: post-window, weak votes
    # against the lock are inert — they cannot switch, cannot advance the
    # confirmation streak, and cannot break it either.
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, WEAK_SWITCH_STIMULUS, WEAK_SWITCH_STIMULUS)
    )
    assert decision.language == "nl"
    assert decision.switches == 0
    assert decision.votes == 5
    assert decision.abstentions == 0
    assert decision.locked is True
    assert decision.reason == clm.REASON_LOCKED


def test_post_window_switch_costs_a_strong_turn_plus_the_streak() -> None:
    # The weak vote is skipped; the first post-lock switch still happens on
    # ONE strong clean turn (FIRST_SWITCH_CONFIRMATIONS = 1).
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, WEAK_SWITCH_STIMULUS, EN_1)
    )
    assert decision.language == "en"
    assert decision.switches == 1
    assert decision.reason == clm.REASON_SWITCHED
    assert decision.votes == 5


def test_explicit_request_switches_regardless_of_weak_votes() -> None:
    # Step 2 is unaffected by strength: even after a refused weak vote, an
    # explicit request switches immediately.
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, WEAK_SWITCH_STIMULUS, "Please reply in English")
    )
    assert decision.language == "en"
    assert decision.reason == clm.REASON_EXPLICIT_REQUEST


def test_first_post_lock_switch_needs_one_clear_turn() -> None:
    decision = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, DE_1))
    assert decision.language == "de"
    assert decision.reason == clm.REASON_SWITCHED
    assert decision.switches == 1
    assert decision.locked is True


def test_subsequent_switch_needs_two_consecutive_turns() -> None:
    refused = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, DE_1, FR_1))
    assert refused.language == "de"  # single unconfirmed French stimulus is refused
    assert refused.reason == clm.REASON_SWITCHED

    confirmed = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, DE_1, FR_1, FR_1)
    )
    assert confirmed.language == "fr"
    assert confirmed.switches == 2
    assert confirmed.reason == clm.REASON_SWITCHED


def test_switch_back_costs_the_same_as_a_new_switch() -> None:
    # No cheap ping-pong: after nl -> de, coming back needs two consecutive turns.
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, DE_1, NL_1, NL_2)
    )
    assert decision.language == "nl"
    assert decision.switches == 2
    assert decision.reason == clm.REASON_SWITCHED

    mid = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, DE_1, NL_1))
    assert mid.language == "de"  # one turn back is not enough


def test_weak_stimulus_quoted_foreign_sentence_does_not_switch() -> None:
    stimulus = 'Klant schreef "Can you please refund the full amount immediately today"'
    decision = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, stimulus))
    assert decision.language == "nl"
    assert decision.reason == clm.REASON_LOCKED
    assert decision.abstentions == 1
    assert decision.switches == 0


def test_weak_stimulus_pasted_machine_text_does_not_switch() -> None:
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, JSON_STIMULUS, FENCED, MAIL_HEADERS)
    )
    assert decision.language == "nl"
    assert decision.reason == clm.REASON_LOCKED
    assert decision.abstentions == 3
    assert decision.votes == 3
    assert decision.switches == 0


# --- The talking-vs-quoting strength rule, end to end ----------------------


def test_clean_sentence_switches_established_language_on_one_turn() -> None:
    # Post-window the FIRST switch still costs exactly one strong turn: a
    # clean, quote-free, identifier-free English sentence overturns an
    # established and locked Dutch conversation.
    decision = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, EN_1))
    assert clm._turn_is_strong_switch(EN_1)
    assert decision.language == "en"
    assert decision.switches == 1
    assert decision.reason == clm.REASON_SWITCHED
    assert decision.locked is True


def test_quoted_span_turn_fails_to_switch() -> None:
    # A clean English sentence that wraps a quoted span is QUOTING, not
    # talking: it votes (the surrounding prose is long enough) but is too weak
    # to overturn the established Dutch. Tested inside the window so only
    # strength — not the confirmation streak — can be what blocks the switch.
    turn = (
        'Please refund the full amount, the order note says "damaged on arrival" '
        "and I want my money back today"
    )
    assert not clm._turn_is_strong_switch(turn)
    decision = clm.resolve_conversation_language(user_turns(NL_1, turn))
    assert decision.language == "nl"
    assert decision.switches == 0
    assert decision.votes == 2  # it voted, but could not switch
    assert decision.abstentions == 0
    assert decision.reason == clm.REASON_ESTABLISHED


def test_identifier_bearing_turn_fails_to_switch() -> None:
    # A technical identifier in the turn — here a header name and a bare code —
    # means the user is naming machine output, not asking to change language.
    # The clean English sentence on the second line cannot switch on its own.
    turn = "X-Error-Code: 500\n" + EN_1
    assert not clm._turn_is_strong_switch(turn)
    decision = clm.resolve_conversation_language(user_turns(NL_1, turn))
    assert decision.language == "nl"
    assert decision.switches == 0
    assert decision.votes == 2
    assert decision.abstentions == 0
    assert decision.reason == clm.REASON_ESTABLISHED


def test_bare_number_turn_stays_strong() -> None:
    # A bare number is ordinary speech about a value, not an identifier: "404"
    # must not weaken the turn, so this clean English sentence still switches
    # an established, locked Dutch conversation on a single turn.
    turn = "How many times should I retry the request after error 404 comes back"
    assert clm._turn_is_strong_switch(turn)
    decision = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, turn))
    assert decision.language == "en"
    assert decision.switches == 1
    assert decision.reason == clm.REASON_SWITCHED


def test_weak_turn_still_confirms_the_current_language() -> None:
    # Weakness only bars a turn from SWITCHING. A turn that names an identifier
    # but agrees with the current language still votes and reinforces it — the
    # opposite of an abstention, which would carry no vote at all.
    turn = "De factuur met ordernummer 2024-0081 klopt niet, helpt u mij?"
    assert not clm._turn_is_strong_switch(turn)
    decision = clm.resolve_conversation_language(user_turns(NL_1, turn))
    assert decision.language == "nl"
    assert decision.votes == 2  # counted as a vote
    assert decision.abstentions == 0
    assert decision.switches == 0


def test_abstaining_turn_never_sets_or_resets_state() -> None:
    # An established English conversation with a Dutch acknowledgement that
    # is below the short-text confidence tier must stay English.
    decision = clm.resolve_conversation_language(
        user_turns(EN_1, EN_2, EN_3, "Dank je wel", EN_1)
    )
    assert decision.language == "en"
    assert decision.abstentions == 1
    assert decision.switches == 0


def test_abstentions_do_not_break_the_confirmation_streak() -> None:
    # Spec: subsequent switches need 2 CONSECUTIVE NON-ABSTAINING turns;
    # abstaining turns are skipped entirely, not treated as breaks.
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, DE_1, FR_1, "Ja", FR_1)
    )
    assert decision.language == "fr"
    assert decision.switches == 2


def test_explicit_request_switches_after_lock_immediately() -> None:
    decision = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, FR_1, FR_1, "Antwoord in het Nederlands alstublieft")
    )
    assert decision.language == "nl"
    assert decision.reason == clm.REASON_EXPLICIT_REQUEST
    assert decision.locked is True


def test_explicit_request_works_despite_brevity_gate() -> None:
    # One word, gate-failing prose — still the strongest signal there is.
    decision = clm.resolve_conversation_language(user_turns(NL_1, NL_2, NL_3, "Spaans?"))
    assert decision.language == "es"
    assert decision.reason == clm.REASON_EXPLICIT_REQUEST


def test_explicit_request_resets_confirmation_cost() -> None:
    # After an explicit request the next switch again costs a single turn,
    # while it would have cost two without the request.
    without = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, DE_1, FR_1, EN_1)
    )
    assert without.language == "de"  # FR was never confirmed (cost 2)

    with_request = clm.resolve_conversation_language(
        user_turns(NL_1, NL_2, NL_3, DE_1, FR_1, "Please answer in English", FR_1)
    )
    assert with_request.language == "fr"  # cost reset to 1 by the request
    assert with_request.switches == 3


def test_explicit_request_establishes_on_first_turn() -> None:
    decision = clm.resolve_conversation_language(user_turns("Please reply in English"))
    assert decision.language == "en"
    assert decision.reason == clm.REASON_EXPLICIT_REQUEST
    assert decision.votes == 1


def test_low_confidence_turn_abstains_with_reason() -> None:
    decision = clm.resolve_conversation_language(user_turns("Thanks a lot"))
    assert decision.language is None
    assert decision.reason == clm.REASON_LOW_CONFIDENCE
    assert decision.abstentions == 1
    assert decision.votes == 0


def test_gate_machine_rejection_and_low_confidence_are_separate_reasons() -> None:
    # Correction 2's telemetry requirement: dumps are now rejected by the
    # proportional gate, so a machine-only conversation reports no_evidence
    # — the identifier never sees these turns and its (un)certainty can no
    # longer leak into the reason. The identification path keeps its own
    # reason (previous test) for turns that DID pass the gate.
    machine_only = clm.resolve_conversation_language(user_turns(PROTOCOL_DUMP, PACKET_DUMP))
    assert machine_only.language is None
    assert machine_only.reason == clm.REASON_NO_EVIDENCE
    assert machine_only.abstentions == 2
    assert machine_only.votes == 0


def test_counter_votes_plus_abstentions_equal_user_turns() -> None:
    messages = user_turns(NL_1, FENCED, EN_1, "Ja", "Antwoord in het Nederlands")
    decision = clm.resolve_conversation_language(messages)
    assert decision.votes + decision.abstentions == 5
    assert decision.votes == 3
    assert decision.language == "nl"


def test_assistant_and_system_context_never_influence_decision() -> None:
    messages = [
        {"role": "system", "content": "You are Klai. " + EN_2 + EN_3},
        {"role": "user", "content": NL_1},
        {"role": "assistant", "content": EN_1 + EN_2 + EN_3},
        {"role": "developer", "content": EN_3},
        {"role": "user", "content": NL_2},
    ]
    decision = clm.resolve_conversation_language(messages)
    assert decision.language == "nl"
    assert decision.votes == 2
    assert decision.switches == 0


# ---------------------------------------------------------------------------
# Message shapes and malformed input
# ---------------------------------------------------------------------------


def test_list_content_parts_vote() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": NL_1},
                {"type": "text", "text": "En kunt u ook de maandelijkse facturatie uitleggen?"},
            ],
        }
    ]
    decision = clm.resolve_conversation_language(messages)
    assert decision.language == "nl"
    assert decision.votes == 1


def test_non_text_parts_are_ignored() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": NL_1},
                {"type": "image_url", "image_url": {"url": "https://example.com/english"}},
                {"type": "text", "text": NL_2},
            ],
        }
    ]
    decision = clm.resolve_conversation_language(messages)
    assert decision.language == "nl"


def test_malformed_entries_do_not_raise() -> None:
    messages = [
        None,
        "junk string",
        42,
        {"role": "user"},
        {"role": "user", "content": None},
        {"role": "user", "content": {"type": "text", "text": "not a list english here"}},
        {"role": "user", "content": [{"text": "part without a type key"}]},
        {"role": "user", "content": NL_1},
    ]
    decision = clm.resolve_conversation_language(messages)  # type: ignore[list-item]
    assert decision.language == "nl"
    # Only the role=="user" entries count as turns; the four malformed user
    # entries all degrade to empty text and abstain, junk entries are skipped.
    assert decision.votes == 1
    assert decision.abstentions == 4


def test_non_list_messages_argument_does_not_raise() -> None:
    decision = clm.resolve_conversation_language(None)  # type: ignore[arg-type]
    assert decision.language is None
    assert decision.reason == clm.REASON_NO_EVIDENCE


def test_resolution_is_pure_and_deterministic() -> None:
    messages = user_turns(NL_1, "Ja", EN_1, FENCED, "Antwoord in het Nederlands", DE_1)
    snapshot = copy.deepcopy(messages)
    first = clm.resolve_conversation_language(messages)
    second = clm.resolve_conversation_language(messages)
    assert first == second
    assert messages == snapshot  # input is never mutated


# ---------------------------------------------------------------------------
# Degradation: langid unavailable
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("detector_unavailable")
def test_detector_unavailable_plain_prose_abstains() -> None:
    decision = clm.resolve_conversation_language(user_turns(NL_1, NL_2))
    assert decision.language is None
    assert decision.reason == clm.REASON_DETECTOR_UNAVAILABLE
    assert decision.abstentions == 2
    assert decision.votes == 0


@pytest.mark.usefixtures("detector_unavailable")
def test_detector_unavailable_machine_text_still_no_evidence() -> None:
    # The gate runs before identification, so machine-only turns keep their
    # normal classification even without a detector. The proportional dumps
    # are included: the gate must do its job with no identifier present at
    # all — its abstentions can never depend on the identifier's uncertainty.
    decision = clm.resolve_conversation_language(
        user_turns(FENCED, JSON_STIMULUS, PROTOCOL_DUMP, PACKET_DUMP)
    )
    assert decision.language is None
    assert decision.reason == clm.REASON_NO_EVIDENCE
    assert decision.abstentions == 4


@pytest.mark.usefixtures("detector_unavailable")
def test_detector_unavailable_explicit_request_still_wins() -> None:
    # An explicit request needs no identification at all.
    decision = clm.resolve_conversation_language(user_turns(NL_1, "In English please"))
    assert decision.language == "en"
    assert decision.reason == clm.REASON_EXPLICIT_REQUEST


def test_detector_recovers_after_unavailable_fixture() -> None:
    # Teardown of the fixture above must have restored normal behaviour.
    decision = clm.resolve_conversation_language(user_turns(NL_1))
    assert decision.language == "nl"
