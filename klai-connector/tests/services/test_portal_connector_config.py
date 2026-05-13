"""Regression test for PortalConnectorConfig.id property.

Adapters (ms_docs, google_drive) read ``connector.id`` while sync_engine
passes a ``PortalConnectorConfig`` dataclass whose field is named
``connector_id``. The ``.id`` property bridges the two. If the property
is removed, every adapter that calls ``connector.id`` will raise
``AttributeError`` at the first sync, exactly as it did on 2026-05-13
before this fix landed.
"""

from __future__ import annotations

from app.services.portal_client import PortalConnectorConfig


def _make_config(connector_id: str = "abc-123") -> PortalConnectorConfig:
    return PortalConnectorConfig(
        connector_id=connector_id,
        kb_id=1,
        kb_slug="test-kb",
        zitadel_org_id="org-1",
        connector_type="ms_docs",
        config={},
        schedule=None,
        is_enabled=True,
    )


def test_id_property_returns_connector_id() -> None:
    cfg = _make_config(connector_id="conn-xyz")
    assert cfg.id == "conn-xyz"
    assert cfg.id == cfg.connector_id


def test_id_property_tracks_renames() -> None:
    """Whatever connector_id is set to, .id mirrors it."""
    cfg = _make_config(connector_id="first")
    assert cfg.id == "first"

    # PortalConnectorConfig is a regular dataclass — fields are mutable
    cfg.connector_id = "second"
    assert cfg.id == "second"
