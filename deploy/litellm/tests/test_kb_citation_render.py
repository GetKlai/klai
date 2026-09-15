"""Path A's cross-path language event (chat_synthesis_complete).

SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-07. Paths B and C have emitted this event
since 2026-05; path A only wrote its language numbers as key=value text inside a
prose log line, so every runbook query scoped to ``service:litellm`` came back
empty and the gap was attributed to a detector the container has carried all
along.
"""

from __future__ import annotations

import logging

import klai_kb_citation_render as ccr

# ---------------------------------------------------------------------------
# chat_synthesis_complete — path A joins the cross-path language event
# ---------------------------------------------------------------------------


def _capture_synthesis_event(caplog):
    """Return the parsed chat_synthesis_complete object, or None."""
    import json as _json

    for record in caplog.records:
        message = record.getMessage()
        if not message.startswith("{"):
            continue
        try:
            parsed = _json.loads(message)
        except ValueError:
            continue
        if parsed.get("event") == "chat_synthesis_complete":
            return parsed
    return None


def test_path_a_emits_chat_synthesis_complete_as_parseable_json(caplog):
    """The event must be one JSON object per line, or Alloy cannot field it.

    Path A used to write its language numbers as key=value text inside a prose
    log line, which is why `event:chat_synthesis_complete` returned nothing for
    service:litellm and the coverage gap looked like a missing detector.
    """
    caplog.set_level(logging.INFO)
    kb_meta = {
        "org_id": 8,
        "request_id": "r-1",
        "chunks_injected": 3,
        "user_query": "How do I change my invoice address?",
        "response_language_target": "en",
    }
    ccr._record_answer_language("Go to Settings and open the Billing tab.", kb_meta)

    event = _capture_synthesis_event(caplog)
    assert event is not None, "no parseable chat_synthesis_complete line was emitted"
    assert event["service"] == "litellm"
    assert event["org_id"] == 8
    assert event["chunks_injected"] == 3
    assert event["query_language_detected"] == "en"
    assert event["response_language_detected"] == "en"
    assert event["language_correctness"] is True


def test_path_a_event_counts_an_english_question_answered_in_dutch(caplog):
    caplog.set_level(logging.INFO)
    kb_meta = {"org_id": 8, "user_query": "How do I change my invoice address?"}
    ccr._record_answer_language("Ga naar Beheer en kies daar Facturen.", kb_meta)

    event = _capture_synthesis_event(caplog)
    assert event is not None
    assert event["query_language_detected"] == "en"
    assert event["response_language_detected"] == "nl"
    assert event["language_correctness"] is False


def test_path_a_event_never_breaks_a_rendered_answer(caplog):
    """Telemetry runs after rendering; a bad kb_meta must not raise."""
    caplog.set_level(logging.INFO)
    ccr._record_answer_language("Ga naar Beheer.", {"user_query": object()})
