"""Support-session row -> response-payload shapers for the partner API.

Pure SQLAlchemy-row / dict shaping for support sessions and messages, lifted out
of ``app/api/partner.py``. No DB execution, no auth — the raw-SQL fetchers and
the route handlers (which own the tenant scoping) keep calling these via the
re-import in ``app.api.partner``.
"""

from __future__ import annotations

from typing import Any


def _mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    return str(value.isoformat())


def _message_payload(row: Any) -> dict[str, Any]:
    data = _mapping(row)
    return {
        "id": str(data.get("id")),
        "role": data.get("role"),
        "content": data.get("content"),
        "draft_body": data.get("draft_body"),
        "sources": data.get("sources") or [],
        "model_alias": data.get("model_alias"),
        "completion_id": data.get("completion_id"),
        "sequence": data.get("sequence"),
        "created_at": _isoformat(data.get("created_at")),
    }


def _session_payload(row: Any, messages: list[dict[str, Any]]) -> dict[str, Any]:
    data = _mapping(row)
    return {
        "id": str(data.get("id")),
        "integration_type": data.get("integration_type"),
        "hubspot_portal_id": data.get("hubspot_portal_id"),
        "hubspot_ticket_id": data.get("hubspot_ticket_id"),
        "contact_id": data.get("contact_id"),
        "subject": data.get("subject_snapshot"),
        "status": data.get("status"),
        "message_count": data.get("message_count") or len(messages),
        "created_at": _isoformat(data.get("created_at")),
        "updated_at": _isoformat(data.get("updated_at")),
        "last_message_at": _isoformat(data.get("last_message_at")),
        "messages": messages,
    }
