"""Moved from deploy/litellm/tests/test_answer_epistemics.py (one-chat-
pipeline slice 3) — pins app.services.answer_epistemics: the pure
inspect_answer_epistemics/strip_answer_contract_markers behaviour.

Dropped from the original file (not moved in this slice, still hook-only):
every case that goes through ``compose_non_streaming_kb_response`` /
``compose_streaming_kb_response`` / ``log_kb_citation_render``
(klai_kb_citation_render — citation/streaming render, slice 2/6) and
``test_answer_policy_forwards_telemetry_and_latest_turn_detection``
(klai_kb_answer_policy, slice 4/5).
"""

from __future__ import annotations

import time

from app.services.answer_epistemics import (
    inspect_answer_epistemics,
    strip_answer_contract_markers,
)

_INCIDENT_QUERY = """Kun je dit analyseren?

Van: Klant <klant@example.nl>
Aan: Support <support@example.nl>
Onderwerp: SIP 404

Volgens ons veroorzaakt een fraudeblokkade deze fout.
"""

_EVIDENCE = [
    {
        "title": "SIP responscodes",
        "heading_path": "4xx > 404 Not Found",
        "text": "Een gebruiker of toestel bestaat niet of is niet toegewezen.",
    }
]

_MARKERS = (
    "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]",
    "[[KLAI_CORRESPONDENCE_KB_EVIDENCE]]",
    "[[KLAI_CORRESPONDENCE_OPEN_QUESTIONS]]",
    "[[KLAI_CORRESPONDENCE_VERIFY_FIRST]]",
)


def _contract_answer(*, order: tuple[int, ...] = (0, 1, 2, 3)) -> str:
    sections = (
        f"{_MARKERS[0]}\nDe afzender stelt dat een fraudeblokkade actief is.",
        f"{_MARKERS[1]}\nDe kennisbank zegt dat 404 een onbekend toestel kan betekenen (E1).",
        f"{_MARKERS[2]}\nDit stelt de precieze oorzaak niet vast.",
        f"{_MARKERS[3]}\nControleer eerst het gekozen nummer.",
    )
    return "\n\n".join(sections[index] for index in order)


def test_sender_only_token_is_measured_when_answer_repeats_it():
    result = inspect_answer_epistemics(
        "De afzender noemt een fraudeblokkade.",
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    provenance = result["claim_provenance"]
    assert provenance["sender_only_tokens_in_answer"] > 0
    assert provenance["correspondence_detected"] is True
    assert "sender_only_tokens" not in provenance
    assert "answer_tokens_unsupported_by_evidence_values" not in provenance


def test_sender_only_token_is_zero_when_answer_does_not_repeat_it():
    result = inspect_answer_epistemics(
        "De kennisbank beschrijft SIP 404 als een niet-bestaand toestel.",
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="full",
    )

    provenance = result["claim_provenance"]
    assert provenance["sender_only_tokens_in_answer"] == 0
    assert provenance["sender_only_tokens"] == []
    assert isinstance(provenance["answer_tokens_unsupported_by_evidence_values"], list)


def test_sender_tokens_exclude_user_instruction_before_correspondence():
    result = inspect_answer_epistemics(
        "Hier is een escalatiesamenvatting van de melding.",
        user_turn="Maak een escalatiesamenvatting.\n\n" + _INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="full",
    )

    provenance = result["claim_provenance"]
    assert "escalatiesamenvatting" not in provenance["sender_only_tokens"]


def test_non_correspondence_keeps_c_empty_and_skips_contract_verification():
    result = inspect_answer_epistemics(
        "Een SIP 404 betekent dat het toestel niet bestaat.",
        user_turn="Wat betekent SIP 404?",
        evidence_chunks=_EVIDENCE,
        correspondence_detected=False,
        telemetry_level="shadow",
    )

    provenance = result["claim_provenance"]
    assert provenance["correspondence_detected"] is False
    assert provenance["sender_only_tokens_in_answer"] == 0
    assert "answer_contract" not in result


def test_well_formed_contract_is_verified_and_markers_are_stripped():
    answer = _contract_answer()

    result = inspect_answer_epistemics(
        answer,
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )
    rendered = strip_answer_contract_markers(answer)

    assert result["answer_contract"] == {
        "satisfied": True,
        "missing_sections": [],
        "order_violation": False,
        "section2_uncited": False,
    }
    assert all(marker not in rendered for marker in _MARKERS)


def test_known_section_suffix_recovers_misspelled_internal_prefix(caplog):
    caplog.set_level("INFO", logger="app.services.answer_epistemics")
    answer = _contract_answer().replace(
        "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]",
        "[[KLAI_CORRESPONSE_SENDER_STATEMENTS]]",
    )

    result = inspect_answer_epistemics(
        answer,
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )
    rendered = strip_answer_contract_markers(answer)

    assert result["answer_contract"]["satisfied"] is True
    assert result["answer_contract"]["normalized_marker_count"] == 1
    assert "[[KLAI_" not in rendered
    drift_records = [record for record in caplog.records if "answer_contract_marker_normalized" in record.getMessage()]
    assert len(drift_records) == 1
    assert drift_records[0].levelname == "WARNING"
    assert drift_records[0].normalized_marker_count == 1


def test_canonical_section_markers_do_not_emit_drift_event(caplog):
    caplog.set_level("INFO", logger="app.services.answer_epistemics")

    inspect_answer_epistemics(
        _contract_answer(),
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    assert not any("answer_contract_marker_normalized" in record.getMessage() for record in caplog.records)


def test_missing_section_is_observed_without_rewriting_answer_prose():
    answer = _contract_answer(order=(0, 1, 3))

    result = inspect_answer_epistemics(
        answer,
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )
    rendered = strip_answer_contract_markers(answer)

    assert result["answer_contract"]["satisfied"] is False
    assert result["answer_contract"]["missing_sections"] == [3]
    assert "De afzender stelt dat een fraudeblokkade actief is." in rendered
    assert "Controleer eerst het gekozen nummer." in rendered


def test_out_of_order_sections_are_observed():
    result = inspect_answer_epistemics(
        _contract_answer(order=(0, 2, 1, 3)),
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    assert result["answer_contract"]["satisfied"] is False
    assert result["answer_contract"]["order_violation"] is True


def test_unexpected_ranked_cause_slot_breaks_the_exact_shape():
    answer = _contract_answer().replace(
        _MARKERS[3],
        "[[KLAI_CORRESPONDENCE_RANKED_CAUSE]]\nEen oorzaak.\n\n" + _MARKERS[3],
    )

    result = inspect_answer_epistemics(
        answer,
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    assert result["answer_contract"]["satisfied"] is False
    assert result["answer_contract"]["order_violation"] is True


def test_section_two_requires_a_real_injected_evidence_label():
    answer = _contract_answer().replace(" (E1)", "")

    result = inspect_answer_epistemics(
        answer,
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    assert result["answer_contract"]["satisfied"] is False
    assert result["answer_contract"]["section2_uncited"] is True


def test_contract_rejects_untyped_preamble_before_first_section():
    result = inspect_answer_epistemics(
        "TL;DR: Fraud is the likely cause.\n\n" + _contract_answer(),
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    assert result["answer_contract"]["satisfied"] is False
    assert result["answer_contract"]["order_violation"] is True


def test_follow_up_verifies_contract_but_has_no_latest_turn_sender_tokens():
    result = inspect_answer_epistemics(
        _contract_answer(),
        user_turn="Waarom?",
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        latest_turn_correspondence_detected=False,
        telemetry_level="shadow",
    )

    assert result["claim_provenance"]["correspondence_detected"] is True
    assert result["claim_provenance"]["sender_only_tokens_in_answer"] == 0
    assert result["answer_contract"]["satisfied"] is True


def test_non_correspondence_marker_like_text_is_byte_identical():
    answer = f"Dit is gewone tekst met {_MARKERS[0]} als letterlijk voorbeeld."

    result = inspect_answer_epistemics(
        answer,
        user_turn="Leg dit voorbeeld uit",
        evidence_chunks=_EVIDENCE,
        correspondence_detected=False,
        telemetry_level="shadow",
    )

    assert "answer_contract" not in result
    assert answer == f"Dit is gewone tekst met {_MARKERS[0]} als letterlijk voorbeeld."


def test_multipart_correspondence_keeps_part_boundary_for_sender_tokens():
    user_turn = "Please analyse this: ---------- Forwarded message ---------- Sender-only hypothesis"

    result = inspect_answer_epistemics(
        "Sender-only hypothesis",
        user_turn=user_turn,
        evidence_chunks=[],
        correspondence_detected=True,
        telemetry_level="full",
        latest_turn_correspondence_detected=True,
    )

    assert "sender-only" in result["claim_provenance"]["sender_only_tokens"]


def test_epistemics_inspection_stays_below_twenty_milliseconds_p95():
    answer = _contract_answer() + (" extra context" * 500)
    durations_ms: list[float] = []

    for _ in range(100):
        started = time.perf_counter()
        inspect_answer_epistemics(
            answer,
            user_turn=_INCIDENT_QUERY,
            evidence_chunks=_EVIDENCE,
            correspondence_detected=True,
            telemetry_level="shadow",
        )
        durations_ms.append((time.perf_counter() - started) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(len(durations_ms) * 0.95)]
    assert p95 < 20.0
