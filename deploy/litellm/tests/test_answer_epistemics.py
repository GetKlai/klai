"""Contract tests for answer provenance and pasted-correspondence shape."""

from __future__ import annotations

import logging
import time

from klai_answer_epistemics import (
    inspect_answer_epistemics,
    strip_answer_contract_markers,
)
from klai_kb_citation_render import (
    KbCitationRenderStats,
    _render_kb_citation_content,
    compose_non_streaming_kb_response,
    compose_streaming_kb_response,
    log_kb_citation_render,
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


def _grounded_meta(**overrides) -> dict:
    return {
        "chat_retrieval_prompt_mode": "open_kb",
        "kb_narrow": False,
        "allowed_image_urls": [],
        "citation_chunks": _EVIDENCE,
        "trusted_sources": [
            {"title": "SIP responscodes", "url": "https://example.test/sip"}
        ],
        "no_citable_sources": False,
        "user_query": _INCIDENT_QUERY,
        "pasted_correspondence_detected": True,
        "telemetry_level": "shadow",
        **overrides,
    }


def test_grounded_render_records_claim_provenance_in_metadata():
    response = {
        "choices": [{"message": {"content": "De afzender noemt een fraudeblokkade."}}]
    }
    kb_meta = _grounded_meta()

    compose_non_streaming_kb_response(response, kb_meta)

    assert kb_meta["claim_provenance"]["sender_only_tokens_in_answer"] > 0
    assert kb_meta["claim_provenance"]["correspondence_detected"] is True


def test_structured_log_emits_counts_but_redacts_token_values(caplog):
    response = {
        "choices": [{"message": {"content": "De afzender noemt een fraudeblokkade."}}]
    }
    kb_meta = _grounded_meta()
    stats = compose_non_streaming_kb_response(response, kb_meta)

    with caplog.at_level(logging.WARNING):
        log_kb_citation_render(
            logging.getLogger("test-answer-epistemics"),
            kb_meta,
            stats,
            stream=False,
        )

    assert "sender_only_tokens_in_answer=" in caplog.text
    assert "answer_tokens_unsupported_by_evidence=" in caplog.text
    assert "correspondence_detected=True" in caplog.text
    assert "sender_only_tokens=<redacted>" in caplog.text
    assert "fraudeblokkade" not in caplog.text


def test_citation_rescue_application_emits_actionable_event(caplog):
    stats = KbCitationRenderStats(
        rendered_messages=1,
        rendered_sources=2,
        citation_decisions=[
            {
                "selected": [
                    {"reason": "supported"},
                    {"reason": "rescued"},
                ],
                "rejected": [],
            }
        ],
    )
    kb_meta = _grounded_meta()

    with caplog.at_level(logging.WARNING):
        log_kb_citation_render(
            logging.getLogger("test-citation-rescue"),
            kb_meta,
            stats,
            stream=False,
        )

    assert "citation_rescue_applied" in caplog.text
    assert "rescued_sources=1" in caplog.text


def test_grounded_answer_logs_epistemics_when_no_citation_is_rendered(caplog):
    response = {"choices": [{"message": {"content": "Een los antwoord."}}]}
    kb_meta = _grounded_meta(pasted_correspondence_detected=False)
    stats = compose_non_streaming_kb_response(response, kb_meta)
    assert stats.rendered_messages == 0

    with caplog.at_level(logging.WARNING):
        log_kb_citation_render(
            logging.getLogger("test-answer-epistemics-only"),
            kb_meta,
            stats,
            stream=False,
        )

    assert "kb_answer_epistemics" in caplog.text
    assert "sender_only_tokens_in_answer=0" in caplog.text


def test_answer_policy_forwards_telemetry_and_latest_turn_detection():
    from klai_kb_answer_policy import KbAnswerPolicy

    policy = KbAnswerPolicy(
        state="chunks_present",
        prompt_mode="open_kb",
        user_provided_content_context=False,
        pasted_correspondence=True,
    )

    meta = policy.to_kb_meta(
        org_id="org",
        user_id="user",
        retrieval_ms=1,
        telemetry_level="full",
        latest_turn_pasted_correspondence_detected=False,
    )

    assert meta["telemetry_level"] == "full"
    assert meta["latest_turn_pasted_correspondence_detected"] is False


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
    caplog.set_level("INFO", logger="klai_answer_epistemics")
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
    drift_records = [
        record
        for record in caplog.records
        if "answer_contract_marker_normalized" in record.getMessage()
    ]
    assert len(drift_records) == 1
    assert drift_records[0].levelname == "WARNING"
    assert drift_records[0].normalized_marker_count == 1


def test_canonical_section_markers_do_not_emit_drift_event(caplog):
    caplog.set_level("INFO", logger="klai_answer_epistemics")

    inspect_answer_epistemics(
        _contract_answer(),
        user_turn=_INCIDENT_QUERY,
        evidence_chunks=_EVIDENCE,
        correspondence_detected=True,
        telemetry_level="shadow",
    )

    assert not any(
        "answer_contract_marker_normalized" in record.getMessage()
        for record in caplog.records
    )


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
    user_turn = (
        "Please analyse this: ---------- Forwarded message ---------- "
        "Sender-only hypothesis"
    )

    result = inspect_answer_epistemics(
        "Sender-only hypothesis",
        user_turn=user_turn,
        evidence_chunks=[],
        correspondence_detected=True,
        telemetry_level="full",
        latest_turn_correspondence_detected=True,
    )

    assert "sender-only" in result["claim_provenance"]["sender_only_tokens"]


def test_non_correspondence_citation_suppression_preserves_answer_text():
    answer = "Attachment answer (e1)."

    rendered, _sources, _no_citable, _decision = _render_kb_citation_content(
        answer,
        allowed_image_urls=set(),
        user_query="What is in the attachment?",
        trusted_sources=[],
        evidence_chunks=[{"title": "KB source", "text": "KB fact"}],
        kb_narrow=False,
        allow_uncited_user_content=True,
        suppress_citations_for_user_content=True,
        strip_correspondence_evidence_labels=False,
    )

    assert rendered == answer


def test_correspondence_suppressed_stream_strips_markers_and_evidence_labels():
    kb_meta = _grounded_meta(
        trusted_sources=[],
        allow_uncited_user_content=True,
        suppress_kb_citations=True,
    )
    item = {
        "choices": [{"delta": {"content": _contract_answer()}, "finish_reason": "stop"}]
    }

    compose_streaming_kb_response(item, kb_meta, flush_stream=True)

    rendered = item["choices"][0]["delta"]["content"]
    assert "KLAI_CORRESPONDENCE" not in rendered
    assert "(E1)" not in rendered


def test_incident_replay_keeps_fraud_hypothesis_only_in_attributed_sender_section():
    """AC-9: deterministic render replay of the production failure shape."""
    response = {"choices": [{"message": {"content": _contract_answer()}}]}
    kb_meta = _grounded_meta()

    compose_non_streaming_kb_response(response, kb_meta)

    rendered = response["choices"][0]["message"]["content"]
    sender_text, remainder = rendered.split("De kennisbank zegt", 1)
    assert "fraudeblokkade" in sender_text
    assert "fraudeblokkade" not in remainder
    assert "meest waarschijnlijke oorzaak" not in rendered
    assert all(marker not in rendered for marker in _MARKERS)
    assert kb_meta["answer_contract"]["satisfied"] is True


def test_streaming_contract_markers_split_across_deltas_never_leak():
    answer = _contract_answer()
    cuts = (17, 86, 151, 224)
    parts: list[str] = []
    start = 0
    for cut in cuts:
        parts.append(answer[start:cut])
        start = cut
    parts.append(answer[start:])
    kb_meta = _grounded_meta()
    visible_parts: list[str] = []

    for index, part in enumerate(parts):
        item = {
            "choices": [
                {
                    "delta": {"content": part},
                    "finish_reason": "stop" if index == len(parts) - 1 else None,
                }
            ]
        }
        compose_streaming_kb_response(
            item, kb_meta, flush_stream=index == len(parts) - 1
        )
        visible_parts.append(item["choices"][0]["delta"]["content"])

    rendered = "".join(visible_parts)
    assert any(part.strip() for part in visible_parts[:-1])
    assert all(marker not in rendered for marker in _MARKERS)
    assert "fraudeblokkade" in rendered
    assert "Controleer eerst het gekozen nummer." in rendered
    assert kb_meta["answer_contract"]["satisfied"] is True


def test_streaming_misspelled_internal_marker_never_leaks():
    answer = _contract_answer().replace(
        "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]",
        "[[KLAI_CORRESPONSE_SENDER_STATEMENTS]]",
    )
    split_at = answer.index("SENDER_STATEMENTS") - 2
    kb_meta = _grounded_meta()
    visible_parts: list[str] = []

    for index, part in enumerate((answer[:split_at], answer[split_at:])):
        item = {
            "choices": [
                {
                    "delta": {"content": part},
                    "finish_reason": "stop" if index == 1 else None,
                }
            ]
        }
        compose_streaming_kb_response(item, kb_meta, flush_stream=index == 1)
        visible_parts.append(item["choices"][0]["delta"]["content"])

    rendered = "".join(visible_parts)
    assert "[[KLAI_" not in rendered
    assert kb_meta["answer_contract"]["satisfied"] is True


def test_open_stream_without_trusted_sources_is_unchanged_but_measured(caplog):
    kb_meta = _grounded_meta(
        pasted_correspondence_detected=False,
        trusted_sources=[],
    )
    first = {"choices": [{"delta": {"content": "Eerste deel "}, "finish_reason": None}]}
    final = {"choices": [{"delta": {"content": "en slot."}, "finish_reason": "stop"}]}

    first_stats = compose_streaming_kb_response(first, kb_meta)
    final_stats = compose_streaming_kb_response(final, kb_meta, flush_stream=True)
    with caplog.at_level(logging.WARNING):
        log_kb_citation_render(
            logging.getLogger("test-stream-epistemics-only"),
            kb_meta,
            final_stats,
            stream=True,
        )

    assert first["choices"][0]["delta"]["content"] == "Eerste deel "
    assert final["choices"][0]["delta"]["content"] == "en slot."
    assert first_stats.rendered_messages == 0
    assert final_stats.rendered_messages == 0
    assert kb_meta["claim_provenance"]["correspondence_detected"] is False
    assert "kb_answer_epistemics" in caplog.text


def test_correspondence_stream_without_trusted_sources_strips_internal_tokens():
    kb_meta = _grounded_meta(trusted_sources=[])
    answer = _contract_answer()
    item = {"choices": [{"delta": {"content": answer}, "finish_reason": "stop"}]}

    compose_streaming_kb_response(item, kb_meta, flush_stream=True)

    rendered = item["choices"][0]["delta"]["content"]
    assert "KLAI_CORRESPONDENCE" not in rendered
    assert "(E1)" not in rendered
    assert kb_meta["answer_contract"]["satisfied"] is True


def test_correspondence_stream_without_evidence_still_strips_markers():
    kb_meta = _grounded_meta(citation_chunks=[], trusted_sources=[])
    answer = _contract_answer().replace(
        "De kennisbank zegt dat 404 een onbekend toestel kan betekenen (E1).",
        "De kennisbank bevat geen bewijs over deze situatie.",
    )
    item = {"choices": [{"delta": {"content": answer}, "finish_reason": "stop"}]}

    compose_streaming_kb_response(item, kb_meta, flush_stream=True)

    rendered = item["choices"][0]["delta"]["content"]
    assert "KLAI_CORRESPONDENCE" not in rendered
    assert "geen bewijs" in rendered
    assert kb_meta["answer_contract"]["satisfied"] is True


def test_correspondence_suppressed_citation_path_strips_evidence_labels():
    kb_meta = _grounded_meta(
        allow_uncited_user_content=True,
        suppress_kb_citations=True,
    )
    response = {"choices": [{"message": {"content": _contract_answer()}}]}

    compose_non_streaming_kb_response(response, kb_meta)

    rendered = response["choices"][0]["message"]["content"]
    assert "(E1)" not in rendered
    assert "KLAI_CORRESPONDENCE" not in rendered


def test_epistemics_failure_is_fail_open(monkeypatch):
    import klai_kb_citation_render as render_module

    def _raise(*args, **kwargs):
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(render_module, "inspect_answer_epistemics", _raise)
    response = {"choices": [{"message": {"content": "Het oorspronkelijke antwoord."}}]}

    compose_non_streaming_kb_response(response, _grounded_meta())

    assert (
        "Het oorspronkelijke antwoord." in response["choices"][0]["message"]["content"]
    )


def test_epistemics_failure_still_strips_internal_markers(monkeypatch):
    import klai_kb_citation_render as render_module

    def _raise(*args, **kwargs):
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(render_module, "inspect_answer_epistemics", _raise)
    response = {"choices": [{"message": {"content": _contract_answer()}}]}

    compose_non_streaming_kb_response(response, _grounded_meta())

    rendered = response["choices"][0]["message"]["content"]
    assert all(marker not in rendered for marker in _MARKERS)
    assert "fraudeblokkade" in rendered


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
    assert durations_ms[94] < 20
