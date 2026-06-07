"""Direct characterization tests for the partner support-session row shapers.

Pins ``_mapping`` / ``_isoformat`` / ``_message_payload`` / ``_session_payload``
BEFORE they are lifted out of ``app/api/partner.py`` into
``app/services/partner_support.py``. They previously had no direct unit tests —
support-session route tests asserted the composed response, not the shapers in
isolation. Reached through ``app.api.partner`` (which re-exports them) so the
same test passes before and after the move.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.services.partner_support import (
    _isoformat,
    _mapping,
    _message_payload,
    _session_payload,
)

# --- _mapping -----------------------------------------------------------------


def test_mapping_none_is_empty():
    assert _mapping(None) == {}


def test_mapping_dict_passthrough():
    d = {"a": 1}
    assert _mapping(d) is d


def test_mapping_row_with_sqlalchemy_mapping_attr():
    row = SimpleNamespace(_mapping={"x": 2})
    assert _mapping(row) == {"x": 2}


def test_mapping_dict_convertible_pairs():
    assert _mapping([("k", "v")]) == {"k": "v"}


def test_mapping_non_convertible_is_empty():
    assert _mapping(5) == {}


# --- _isoformat ---------------------------------------------------------------


def test_isoformat_none_is_none():
    assert _isoformat(None) is None


def test_isoformat_datetime():
    assert _isoformat(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"


# --- _message_payload ---------------------------------------------------------


def test_message_payload_shapes_row():
    row = {
        "id": 7,
        "role": "user",
        "content": "hi",
        "draft_body": None,
        "sources": None,
        "model_alias": "m",
        "completion_id": "c",
        "sequence": 3,
        "created_at": datetime(2026, 1, 2, 3, 4, 5),
    }
    assert _message_payload(row) == {
        "id": "7",
        "role": "user",
        "content": "hi",
        "draft_body": None,
        "sources": [],  # None -> []
        "model_alias": "m",
        "completion_id": "c",
        "sequence": 3,
        "created_at": "2026-01-02T03:04:05",
    }


# --- _session_payload ---------------------------------------------------------


def test_session_payload_shapes_row_and_uses_subject_snapshot():
    row = {
        "id": 9,
        "integration_type": "hubspot",
        "hubspot_portal_id": "p1",
        "hubspot_ticket_id": "t1",
        "contact_id": "c1",
        "subject_snapshot": "Subj",
        "status": "open",
        "message_count": 4,
        "created_at": datetime(2026, 1, 2, 3, 4, 5),
        "updated_at": datetime(2026, 1, 2, 3, 4, 6),
        "last_message_at": None,
    }
    messages = [{"id": "1"}]
    assert _session_payload(row, messages) == {
        "id": "9",
        "integration_type": "hubspot",
        "hubspot_portal_id": "p1",
        "hubspot_ticket_id": "t1",
        "contact_id": "c1",
        "subject": "Subj",
        "status": "open",
        "message_count": 4,
        "created_at": "2026-01-02T03:04:05",
        "updated_at": "2026-01-02T03:04:06",
        "last_message_at": None,
        "messages": messages,
    }


def test_session_payload_message_count_falls_back_to_len_messages():
    row = {"id": 1, "message_count": None}
    messages = [{"id": "a"}, {"id": "b"}]
    assert _session_payload(row, messages)["message_count"] == 2
