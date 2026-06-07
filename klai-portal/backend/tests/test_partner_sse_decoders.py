"""Direct characterization tests for the partner chat-completion wire decoders.

Pins ``_parse_audit_sse_chunk`` (SSE ``data:`` blocks) and
``_extract_assistant_text_and_sources`` (non-streaming result) BEFORE they are
lifted out of ``app/api/partner.py`` into ``app/services/partner_sse.py``. They
previously had only indirect coverage via the streaming/non-streaming chat
tests. Reached through ``app.api.partner`` (which re-exports them) so the same
test passes before and after the move.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.api.partner import (
    _extract_assistant_text_and_sources,
    _parse_audit_sse_chunk,
)

# --- _parse_audit_sse_chunk ---------------------------------------------------


def test_parse_sse_single_content_block():
    chunk = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
    assert _parse_audit_sse_chunk(chunk) == ("Hello", None)


def test_parse_sse_accumulates_content_across_data_lines():
    chunk = b'data: {"choices":[{"delta":{"content":"a"}}]}\ndata: {"choices":[{"delta":{"content":"b"}}]}'
    assert _parse_audit_sse_chunk(chunk) == ("ab", None)


def test_parse_sse_filters_sources_to_dicts():
    chunk = b'data: {"choices":[{"delta":{"sources":[{"id":1},"bad",{"id":2}]}}]}'
    text, sources = _parse_audit_sse_chunk(chunk)
    assert text is None
    assert sources == [{"id": 1}, {"id": 2}]


def test_parse_sse_skips_done_and_empty_and_malformed_and_non_data():
    chunk = (
        b"event: ping\n"  # non-data line
        b"data: \n"  # empty payload
        b"data: [DONE]\n"  # sentinel
        b"data: not-json\n"  # malformed JSON
    )
    assert _parse_audit_sse_chunk(chunk) == (None, None)


def test_parse_sse_no_content_or_sources_returns_none_none():
    chunk = b'data: {"choices":[{"delta":{}}]}'
    assert _parse_audit_sse_chunk(chunk) == (None, None)


# --- _extract_assistant_text_and_sources -------------------------------------


def test_extract_from_dict_result():
    result = {"choices": [{"message": {"content": "Hi", "sources": [{"u": 1}, "x", {"u": 2}]}}]}
    assert _extract_assistant_text_and_sources(result) == ("Hi", [{"u": 1}, {"u": 2}])


def test_extract_from_model_dump_object():
    payload = {"choices": [{"message": {"content": "Yo", "sources": [{"u": 9}]}}]}
    result = SimpleNamespace(model_dump=lambda: payload)
    assert _extract_assistant_text_and_sources(result) == ("Yo", [{"u": 9}])


def test_extract_neither_dict_nor_model_dump_returns_empty():
    assert _extract_assistant_text_and_sources(42) == ("", None)


def test_extract_no_choices_returns_empty():
    assert _extract_assistant_text_and_sources({"choices": []}) == ("", None)


def test_extract_none_content_becomes_empty_string():
    result = {"choices": [{"message": {"content": None}}]}
    assert _extract_assistant_text_and_sources(result) == ("", None)


def test_extract_sources_not_a_list_returns_none():
    result = {"choices": [{"message": {"content": "x", "sources": "nope"}}]}
    assert _extract_assistant_text_and_sources(result) == ("x", None)
