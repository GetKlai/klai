"""SPEC-PRIVACY-QUERY-SHADOW-001 REQ-13 — anti-leakage processor tests.

The processor is a defense-in-depth net behind the explicit gating in
retrieve.py / klai_knowledge.py. Tests cover both the positive path (event
fields survive in 'full' mode) and the negative path (fields are stripped
when telemetry_level is anything else).
"""

from __future__ import annotations

from log_utils.structlog_setup import _strip_query_fields_processor


def _run(event_dict: dict) -> dict:
    """Invoke the processor with the same calling shape structlog uses."""
    # The processor mutates and returns the dict; tests assert against the
    # returned value to keep the contract explicit.
    return _strip_query_fields_processor(None, "info", dict(event_dict))


def test_full_mode_preserves_query_fields() -> None:
    """Positive control: in 'full' mode every leaked field stays put."""
    out = _run(
        {
            "event": "retrieval_decision_record",
            "telemetry_level": "full",
            "raw_query": "Hoe koppel ik X?",
            "rewritten_query": "Hoe koppel ik X met Y?",
            "query": "Hoe koppel ik X?",
            "query_text": "...",
            "query_resolved": "...",
            "coreference_rewrite": {"original": "...", "resolved": "..."},
            "request_id": "abc",
        }
    )
    assert out["raw_query"] == "Hoe koppel ik X?"
    assert out["rewritten_query"] == "Hoe koppel ik X met Y?"
    assert out["coreference_rewrite"]["original"] == "..."
    assert out["request_id"] == "abc"


def test_shadow_mode_strips_query_fields_on_decision_record() -> None:
    out = _run(
        {
            "event": "retrieval_decision_record",
            "telemetry_level": "shadow",
            "raw_query": "Hoe koppel ik X?",
            "rewritten_query": "...",
            "query": "...",
            "query_text": "...",
            "query_resolved": "...",
            "coreference_rewrite": {"original": "leaked", "resolved": "leaked"},
            "request_id": "abc",
            "band": "low",
        }
    )
    # All raw-query-shaped fields gone:
    for key in (
        "raw_query",
        "rewritten_query",
        "query",
        "query_text",
        "query_resolved",
        "coreference_rewrite",
    ):
        assert key not in out, f"field {key} should have been stripped"
    # Other fields preserved.
    assert out["request_id"] == "abc"
    assert out["band"] == "low"


def test_off_mode_strips_query_fields_on_query_rewrite() -> None:
    out = _run(
        {
            "event": "query_rewrite",
            "telemetry_level": "off",
            "raw_query": "Hoe koppel ik X?",
            "rewritten_query": "...",
            "rewrite_ms": 12.3,
            "status": "ok",
        }
    )
    assert "raw_query" not in out
    assert "rewritten_query" not in out
    assert out["rewrite_ms"] == 12.3
    assert out["status"] == "ok"


def test_unrelated_events_pass_through_unchanged() -> None:
    """Events whose name doesn't match the prefixes are never touched."""
    out = _run(
        {
            "event": "auth_check_passed",
            "telemetry_level": "shadow",
            "query": "this is not actually a query — keep as-is",
            "user_id": "u1",
        }
    )
    # The processor only acts on retrieval_decision_record / query_rewrite
    # event names. An auth event must not be censored even if it happens
    # to carry a 'query' kwarg.
    assert out["query"] == "this is not actually a query — keep as-is"


def test_default_strip_when_level_absent() -> None:
    """Missing telemetry_level → privacy-side default (strip)."""
    out = _run(
        {
            "event": "retrieval_decision_record",
            # no telemetry_level set
            "raw_query": "leaked",
            "request_id": "abc",
        }
    )
    assert "raw_query" not in out
    assert out["request_id"] == "abc"


def test_event_prefix_match() -> None:
    """retrieval_decision_record_v2 (hypothetical future name) also gated."""
    out = _run(
        {
            "event": "retrieval_decision_record_v2",
            "telemetry_level": "shadow",
            "raw_query": "leaked",
        }
    )
    assert "raw_query" not in out


def test_does_not_explode_on_non_string_event() -> None:
    """Defensive: a malformed event_dict doesn't break the chain."""
    out = _run(
        {
            "event": 12345,  # type: ignore[dict-item]
            "telemetry_level": "shadow",
            "raw_query": "untouched because event isn't a string",
        }
    )
    # Non-string event → processor is a no-op (defensive).
    assert out["raw_query"] == "untouched because event isn't a string"


def test_contextvar_binding_propagates_into_event_dict() -> None:
    """Integration: ``telemetry_level`` bound via structlog.contextvars
    must reach the processor's view of the event dict (via the
    ``merge_contextvars`` processor that runs upstream in setup_logging).

    Caller (retrieval-api/api/retrieve.py) does
    ``structlog.contextvars.bind_contextvars(telemetry_level=req.telemetry_level)``
    once per request. From that moment, every subsequent log line in
    the same async-task carries the level — even ones that don't pass
    it as a kwarg. This test pins that contract because REQ-13's safety
    net depends on it.
    """
    import structlog

    structlog.contextvars.clear_contextvars()
    try:
        # Simulate retrieve.py binding the level once per request.
        structlog.contextvars.bind_contextvars(telemetry_level="full")

        # Simulate structlog's merge_contextvars processor running
        # upstream of ours: it merges contextvars into the event_dict.
        merged: dict = dict(structlog.contextvars.get_contextvars())
        merged["event"] = "retrieval_decision_record"
        merged["coreference_rewrite"] = {"original": "S3CR3T", "resolved": "..."}

        out = _run(merged)
        assert out["coreference_rewrite"]["original"] == "S3CR3T", (
            "contextvar telemetry_level=full should preserve the field"
        )

        # Flip to shadow and re-merge — same event must now be stripped.
        structlog.contextvars.bind_contextvars(telemetry_level="shadow")
        merged2: dict = dict(structlog.contextvars.get_contextvars())
        merged2["event"] = "retrieval_decision_record"
        merged2["coreference_rewrite"] = {"original": "S3CR3T", "resolved": "..."}

        out2 = _run(merged2)
        assert "coreference_rewrite" not in out2, (
            "contextvar telemetry_level=shadow must strip the field even when "
            "the caller did not explicitly pass telemetry_level as a kwarg"
        )
    finally:
        structlog.contextvars.clear_contextvars()
