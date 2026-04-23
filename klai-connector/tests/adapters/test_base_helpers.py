"""Tests for app.adapters.base helpers.

Covers ``resolve_connector_id`` — the bridge between
:class:`PortalConnectorConfig` (production runtime, exposes
``connector_id: str``) and legacy ``SimpleNamespace(id=...)`` test
stubs that pre-date SPEC-CONNECTOR-CLEANUP-001 Fase 4.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.adapters.base import resolve_connector_id


def test_returns_connector_id_when_present() -> None:
    """PortalConnectorConfig-shaped object: returns the canonical ``connector_id``."""
    obj = SimpleNamespace(connector_id="abc-123", id="should-be-ignored")
    assert resolve_connector_id(obj) == "abc-123"


def test_falls_back_to_id_when_connector_id_missing() -> None:
    """Legacy stubs without ``connector_id``: falls back to ``.id``."""
    obj = SimpleNamespace(id="legacy-uuid")
    assert resolve_connector_id(obj) == "legacy-uuid"


def test_falls_back_to_id_when_connector_id_empty_string() -> None:
    """``connector_id=""`` is treated as absent — empty truthy short-circuits."""
    obj = SimpleNamespace(connector_id="", id="legacy-uuid")
    assert resolve_connector_id(obj) == "legacy-uuid"


def test_returns_empty_string_when_neither_attribute_set() -> None:
    """Fail-safe: no canonical id and no legacy id → empty string, no AttributeError."""
    obj = SimpleNamespace()
    assert resolve_connector_id(obj) == ""


def test_returns_empty_string_when_both_attributes_empty() -> None:
    """Both attributes set to empty string → empty string."""
    obj = SimpleNamespace(connector_id="", id="")
    assert resolve_connector_id(obj) == ""


def test_coerces_uuid_id_to_string() -> None:
    """Legacy ORM had ``id: UUID``; helper must always return ``str``."""
    legacy_uuid = uuid.UUID("11111111-2222-3333-4444-555555555555")
    obj = SimpleNamespace(id=legacy_uuid)
    result = resolve_connector_id(obj)
    assert isinstance(result, str)
    assert result == "11111111-2222-3333-4444-555555555555"


def test_coerces_non_string_connector_id_to_string() -> None:
    """Defensive: even if a future shape passes a non-string, helper returns ``str``."""
    obj = SimpleNamespace(connector_id=42)
    result = resolve_connector_id(obj)
    assert isinstance(result, str)
    assert result == "42"
